"""Qt-owned live commands, display delivery and ordered shutdown."""
from __future__ import annotations
from copy import deepcopy
import ctypes
from ctypes import wintypes
import os
import json
import math
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
from client_problem_messages import operation_problem


def _presentation_error_details(error):
    details = getattr(error, "diagnostics", None)
    return str(error) + ("\n" + json.dumps(details, ensure_ascii=False) if details else "")


def _aligned_material_scope(payload):
    """Content scope, deliberately separate from per-frame live authorization."""
    return tuple(deepcopy(payload.get(key)) for key in
                 ("roundId", "mode", "profileId", "mapId", "floor", "resourceIdentity",
                  "routeWidth", "routeHeight"))


class LiveImages(QQuickImageProvider):
    """Source, staged target and reference images, bounded without disk encoding."""
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._lock = threading.RLock()
        self._images = {}
        self._staged_key = None
        self._material = None
        self._staged_material = None

    def publish(self, key, pixels, *, material_scope=None):
        import numpy as np
        pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
        if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.shape[0] * pixels.shape[1] > 20_000_000:
            raise ValueError("Invalid RGBA display payload")
        image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format_RGBA8888).copy()
        with self._lock:
            # Frame grants expire independently of immutable display pixels.
            # Reusing a *material* URL never reuses its authorization token,
            # geometry or ACK. Compare bytes too: resource names alone cannot
            # prove that a changed author layer contains the same image.
            if (material_scope is not None and not str(key).startswith("pending:")
                    and self._material is not None):
                scope, previous_key = self._material
                if (scope == material_scope and previous_key in self._images
                        and self._images[previous_key] == image):
                    return "image://live/" + previous_key
            if str(key).startswith("reference:"):
                self._images = {name:value for name,value in self._images.items()
                                if not name.startswith("reference:")}
            self._images.pop(str(key), None)
            self._images[str(key)] = image
            if str(key).startswith("pending:"):
                self._staged_key = str(key)
                self._staged_material = (deepcopy(material_scope), str(key)) if material_scope is not None else None
            elif material_scope is not None:
                self._material = (deepcopy(material_scope), str(key))
            while len(self._images) > (3 if self._staged_key else 2):
                self._images.pop(next(name for name in self._images if not name.startswith("reference:")))
        return "image://live/" + str(key)

    def finish_staging(self, token, accepted):
        key = "pending:" + str(token)
        with self._lock:
            if self._staged_key != key:
                return
            self._staged_key = None
            if not accepted:
                self._images.pop(key, None)
            elif self._staged_material is not None:
                self._material = self._staged_material
            self._staged_material = None
            while len(self._images) > 2:
                self._images.pop(next(name for name in self._images if not name.startswith("reference:") and name != key))

    def requestImage(self, identifier, size, requestedSize):
        with self._lock:
            image = self._images.get(identifier, QImage()).copy()
        if size is not None:
            size.setWidth(image.width()); size.setHeight(image.height())
        return image

    def clear(self):
        with self._lock:
            self._images.clear()
            self._staged_key = None
            self._material = self._staged_material = None

    def clear_aligned(self):
        with self._lock:
            self._images = {key:value for key,value in self._images.items() if key.startswith("reference:")}
            self._staged_key = None
            self._material = self._staged_material = None

    def clear_reference(self):
        with self._lock:
            self._images = {key:value for key,value in self._images.items() if not key.startswith("reference:")}


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
                             processCreateTime=process.create_time(), executable=process.exe(),
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
                    "星图.exe", "第五人格小助手.exe", "第五人格小抄助手.exe", "xingtuqtclienttest.exe"}:
                return True
        except (psutil.Error, OSError):
            continue
    return False


class ClientCoordinator(QObject):
    changed = Signal()
    pageRequested = Signal(str)
    quitRequested = Signal()
    runtimeEvent = Signal(object)
    controlVisibilityChanged = Signal(bool, str)
    errorReported = Signal(object)
    traceReported = Signal(object)
    lifecycleEvent = Signal(object)
    notificationRequested = Signal(str, str, str)

    def __init__(self, *, backend, settings, resources, session, windows, native_input,
                 capture, runtime, images, window_catalog=game_window_rows):
        super().__init__()
        self.backend, self.settings, self.resources = backend, settings, resources
        self.session, self.windows, self.input = session, windows, native_input
        self.capture, self.runtime, self.images = capture, runtime, images
        self._catalog = window_catalog
        self._window_rows = []
        self._automatic_live = window_catalog is game_window_rows
        self._binding_identity = None
        self._last_catalog_notice = None
        self._last_round_active = False
        self._last_prepared_round = None
        self._last_round_preparing = False
        self._notified = set()
        self._bound = ""
        self._message = "选择游戏窗口后，再按需启用实时功能"
        self._reference = {}
        self._focus_notice = ""
        self._aligned = {}
        self._pending_aligned = {}
        self._accepted_material = {}
        self._last_material_ack = None
        from route_display_notice import FallbackNoticeTracker
        self._fallback_notices = FallbackNoticeTracker()
        self._edit = None
        self._before_edit = None
        self._edit_generation = 0
        self._reference_clickthrough = True
        self._reference_placement_signature = None
        self._reference_preview_provider = None
        self._reference_preview_context = None
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
        self._shortcut_recording = bool(getattr(settings, "shortcutRecording", False))
        self._requested_action = ""
        self._map_input_sequence = 0
        self._focus_loss_map_sequence = None
        self._pointer_drag = None
        self._pointer_sequence = 0
        self._waiting_foreground = False
        self._map_was_open = False
        self._closing = False
        self._close_ready = False
        self._last_close_wait = None
        from client_game_focus import GameFocusService
        self._game_focus = GameFocusService(lambda: int(self._bound) if self._bound else None,
            is_closed=lambda: self._closed or self._closing, parent=self)
        self.close_writers = ()
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(40)
        self._close_timer.timeout.connect(self._finish_close)
        self._material_timer = QTimer(self)
        self._material_timer.setSingleShot(True)
        self._material_timer.setInterval(5000)
        self._material_timer.timeout.connect(lambda: self._cancel_pending_material("target-material-timeout"))
        self._discovery_timer = QTimer(self)
        self._discovery_timer.setInterval(750)
        self._discovery_timer.timeout.connect(self._discover_game)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self.startLiveServices)
        guard = getattr(settings, "setModeChangeGuard", None)
        if callable(guard):
            guard(lambda: not self.session.snapshot.get("roundActive", False))
        settings.changed.connect(self._draft_changed)
        settings.saved.connect(self._settings_saved)
        recording_signal = getattr(settings, "shortcutRecordingChanged", None)
        if recording_signal is not None:
            recording_signal.connect(self._set_shortcut_recording)
        session.changed.connect(self._session_changed)
        session.actionRequested.connect(self._action_requested)
        resources.resourcesChanged.connect(self._resources_changed)
        native_input.actionTriggered.connect(self._native_action)
        map_signal = getattr(native_input, "mapInputRequested", None)
        ordered_setter = getattr(native_input, "setOrderedMapInputEnabled", None)
        self._ordered_map_input = map_signal is not None and callable(ordered_setter)
        if self._ordered_map_input:
            map_signal.connect(self._map_input_requested)
            ordered_setter(True)
        pointer_signal = getattr(native_input, "gamePointerEvent" if self._ordered_map_input else "gamePointerPressed", None)
        pointer_provider = getattr(runtime, "game_floor_click_context", None)
        pointer_setter = getattr(native_input, "setGamePointerContextProvider", None)
        if pointer_signal is not None and callable(pointer_provider) and callable(pointer_setter):
            pointer_setter(pointer_provider)
            pointer_signal.connect(self._game_pointer_event if self._ordered_map_input else self._game_pointer_pressed)
        native_input.changed.connect(self.changed)
        native_input.changed.connect(self._input_changed)
        backend.replayOverlayRequested.connect(self.showAlignedReplay)
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
        if self._automatic_live:
            self._startup_timer.start(0)

    message = Property(str, lambda s: s._message, notify=changed)
    gameWindows = Property("QVariantList", lambda s: deepcopy(s._window_rows), notify=changed)
    boundWindow = Property(str, lambda s: s._bound, notify=changed)
    referenceData = Property("QVariantMap", lambda s: dict(s._reference), notify=changed)
    referenceEditing = Property(bool, lambda s: s._edit is not None, notify=changed)
    alignedData = Property("QVariantMap", lambda s: dict(s._aligned), notify=changed)
    pendingAlignedData = Property("QVariantMap", lambda s: dict(s._pending_aligned), notify=changed)
    inputEnabled = Property(bool, lambda s: s.input.enabled, notify=changed)
    closing = Property(bool, lambda s: s._closing, notify=changed)
    closeReady = Property(bool, lambda s: s._close_ready, notify=changed)
    textEntryActive = Property(bool, lambda s: s._text_entry or s._shortcut_recording, notify=changed)

    def _trace(self, stage, *, state=None, payload=None, reason="", window=None, **extra):
        data = payload or state or {}
        row = dict(role="presentation", stage=stage, reason=reason,
                   mode=data.get("mode"), groupid=data.get("groupId"),
                   identityPhase=data.get("identityPhase"), provisional=data.get("provisional"),
                   durablemap=data.get("durableMapId", data.get("mapId")), floor=data.get("floor"),
                   round=data.get("roundId"), cycle=data.get("cycleId"), frame=data.get("frameId"),
                   generation=data.get("generation"), token=data.get("token"))
        if window is not None:
            row["window"] = window
        row.update(extra)
        self.traceReported.emit(row)

    @Slot(str, "QVariantMap")
    def reportPresentation(self, name, details):
        if name not in {"aligned", "reference"} or self._closed:
            return
        data = self._aligned if name == "aligned" else self._reference
        self._trace("surface-state", payload=data, window=dict(details, name=name))
        if name == "reference" and data and not self._closing:
            self._restore_reference_placement(details.get("preferredSize"))
        if name == "aligned" and not self._closing:
            self._accept_current_material(data, details)
        if (details.get("imageStatus") == "error" and details.get("visible")
                and details.get("sourceMatchesPayload", True) and not self._closing):
            key = ("surface-error", name, data.get("key", data.get("token")))
            if key not in self._notified:
                self._notified.add(key)
                summary = "地图图像读取失败，详细原因已保存在帮助页的“最近问题”。"
                self.errorReported.emit(dict(title="地图显示未完成", summary=summary,
                    raw=str(details), phase="presentation", action=name, kind="error"))
                self.notificationRequested.emit("地图显示未完成", summary, "error")

    def _accept_current_material(self, data, details):
        """Image readiness acknowledges material only, never identity or a pose."""
        token = data.get("token")
        if (not token or details.get("token") != token or not details.get("visible")
                or details.get("materialReady") is not True
                or details.get("sourceMatchesPayload") is not True
                or details.get("validPayload") is not True or not data.get("liveAuthorization")):
            return
        getter = getattr(self.runtime, "render_payload", None)
        current = getter(token) if callable(getter) else None
        if not current or current.get("token") != token or not current.get("liveAuthorization"):
            return
        geometry = details.get("geometry", ())
        import math
        expected = (data.get("originX"), data.get("originY"),
                    math.ceil(data["clientWidth"] / data["deviceScale"]),
                    math.ceil(data["clientHeight"] / data["deviceScale"]))
        if len(geometry) != 4 or any(not isinstance(a, (int, float)) or not math.isfinite(a)
                                    or abs(a-b) > 1 for a,b in zip(geometry, expected)):
            return
        acknowledge = getattr(self.runtime, "acknowledge_render_material", None)
        if self._last_material_ack != token and callable(acknowledge):
            acknowledge(token)
            self._last_material_ack = token
        if current.get("provisional") or current.get("previewOnly"):
            return
        from route_display_notice import DisplayNoticeContext
        def context(payload):
            return DisplayNoticeContext(payload.get("roundId"), payload.get("mode"),
                payload.get("mapId"), payload.get("floor"), repr(payload.get("resourceIdentity")),
                (payload.get("cycleId"), payload.get("frameId"), payload.get("generation")))
        self._fallback_notices.begin_round(current.get("roundId"))
        notice = self._fallback_notices.consume(context(data), context(current),
            display_kind=current.get("displayKind", ""), author_status=current.get("authorStatus", "none"),
            current_commit=True)
        if notice:
            self.notificationRequested.emit("地图显示已就绪", notice, "info")

    def _cancel_pending_material(self, reason):
        pending = self._pending_aligned
        self._pending_aligned = {}
        self._material_timer.stop()
        if pending:
            acknowledge = getattr(self.runtime, "acknowledge_sibling", None)
            result = None
            if callable(acknowledge):
                result = acknowledge(pending["token"], False, reason=reason)
            finish = getattr(self.images, "finish_staging", None)
            if callable(finish):
                finish(pending["token"], False)
            self._trace("sibling-material-rejected", payload=pending, reason=reason)
            if reason == "target-material-timeout":
                self._report_display_failure(pending, reason, reason)
                if isinstance(result, dict) and not result.get("sourceRetained"):
                    self._present_runtime_state(self.runtime.snapshot(), trigger="sibling-source-expired")
            self.changed.emit()

    def _prepare_sibling_material(self, event):
        token = event.get("token")
        getter = getattr(self.runtime, "pending_sibling_payload", None)
        payload = getter(token) if token and callable(getter) else None
        if not payload or not payload.get("liveAuthorization"):
            self._trace("drop", payload=event, reason="pending-sibling-not-current-live")
            return
        if self._pending_aligned.get("token") == token:
            return
        self._cancel_pending_material("target-material-replaced")
        public = {key: value for key, value in payload.items() if key != "pixels"}
        try:
            self._convert_client_geometry(public)
            path = public.pop("routePath", None)
            if path:
                public["routeUrl"] = QUrl.fromLocalFile(str(path)).toString()
            elif payload.get("pixels") is not None:
                public["routeUrl"] = self.images.publish("pending:" + str(token), payload["pixels"],
                                                        material_scope=_aligned_material_scope(payload))
            else:
                raise ValueError("target-route-material-unavailable")
        except (OSError, ValueError) as error:
            result = self.runtime.acknowledge_sibling(token, False, reason="target-material-invalid")
            self._trace("sibling-material-rejected", payload=payload, reason="target-material-invalid",
                        diag=dict(error=str(error)))
            self._report_display_failure(payload, "target-material-invalid", _presentation_error_details(error))
            if isinstance(result, dict) and not result.get("sourceRetained"):
                self._present_runtime_state(self.runtime.snapshot(), trigger="sibling-source-expired")
            return
        self._pending_aligned = public
        self._material_timer.start()
        self._trace("sibling-material-loading", payload=public)
        self.changed.emit()

    @Slot(str, bool)
    def acceptSiblingMaterial(self, token, accepted):
        pending = self._pending_aligned
        if self._closed or self._closing or not pending or pending.get("token") != token:
            return
        acknowledge = getattr(self.runtime, "acknowledge_sibling", None)
        if not callable(acknowledge):
            return
        self._material_timer.stop()
        result = acknowledge(token, bool(accepted), reason="target-material-ready" if accepted else "target-material-rejected")
        self._pending_aligned = {}
        committed = bool(accepted and isinstance(result, dict) and result.get("success"))
        if committed:
            self._accepted_material = pending
            self._present_runtime_state(self.runtime.snapshot(), trigger="sibling-material-accepted")
            if not self.session.busy:
                self.session.refreshRuntime()
        else:
            self._trace("sibling-material-rejected", payload=pending,
                        reason=(result or {}).get("reason", "target-material-rejected"))
            if not accepted:
                self._report_display_failure(pending, "target-material-rejected", "Target Image rejected the route material")
            if isinstance(result, dict) and not result.get("sourceRetained"):
                self._present_runtime_state(self.runtime.snapshot(), trigger="sibling-source-expired")
        finish = getattr(self.images, "finish_staging", None)
        if callable(finish):
            finish(token, committed)
        self.changed.emit()

    def _status_text(self):
        if self._focus_notice:
            return self._focus_notice
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
        return "已准备本局，等待游戏窗口并打开地图" if state.get("roundPreparing") else "游戏窗口已连接，可以开始本局" if self._bound else "可先准备本局，启动游戏并打开地图后开始"

    runtimeStatus = Property(str, _status_text, notify=changed)

    @Slot(object)
    def _cancel_session_frame(self, reason):
        cancel = getattr(self.session, "cancelPending", None)
        if callable(cancel):
            cancel(reason)

    def _clear_focus_return_plan(self):
        self._waiting_foreground = False
        self._map_was_open = False
        self._focus_loss_map_sequence = None

    def _reset_map_input(self, reason, *, open_intent=False):
        if reason != "foreground-lost":
            # Only the first focus loss preserves a same-round return plan.
            self._clear_focus_return_plan()
        self._cancel_pointer_drag(reason)
        reset = getattr(self.input, "resetMapInput", None)
        if callable(reset):
            reset(bool(open_intent))

    def _close_map_from_input(self, payload):
        # Close is an urgent lifetime boundary, not an idempotent queued job.
        # Retain the independently correct static reference and this round's ID.
        self._reset_map_input("map-closed")
        self._revoke_runtime("map-closed")
        self._cancel_session_frame("map-closed")
        self._map_was_open = False
        self.session.refreshRuntime()
        enqueue = getattr(self.session, "dispatchWithPayload", None)
        if callable(enqueue):
            enqueue("close_map", dict(payload, openIntent=False))
        else:
            self.session.dispatch("close_map")  # Legacy injected fixture seam.

    @Slot(object)
    def _map_input_requested(self, event):
        if self._closing or self._closed or not isinstance(event, dict):
            return
        sequence, opening = event.get("sequence"), event.get("openIntent")
        created = event.get("createdAt")
        if (type(sequence) is not int or sequence <= self._map_input_sequence
                or type(opening) is not bool or isinstance(created, bool)
                or not isinstance(created, (int, float)) or not math.isfinite(created)
                or not self._bound or event.get("hwnd") != int(self._bound)):
            return
        try:
            if self._native_windows.foreground_window() != int(self._bound):
                return
        except OSError:
            return
        self._map_input_sequence = sequence
        # A new physical edge supersedes an older focus-return capture plan.
        self._waiting_foreground = False
        self._map_was_open = False
        payload = dict(inputSequence=sequence, openIntent=opening,
                       inputCreatedAt=created, inputSource=event.get("source", "hotkey"))
        if not opening:
            self._close_map_from_input(payload)
        elif (self.resources.busy or self.resources.pendingKind or self._pending_context
              or self.session.snapshot.get("resourceLocked")):
            self._reset_map_input("map-input-resources-busy")
            self._message = "正在准备地图资源，请稍后再打开地图"
            self.changed.emit()
        else:
            self._cancel_pointer_drag("map-key-open")
            self._revoke_runtime("map-key-open")
            # Runtime owns atomic ensure-start + original map preparation.
            # Distinct sequence payloads cannot be swallowed by Session dedup.
            self.session.dispatchWithPayload("map", payload)

    @staticmethod
    def _pointer_context_fields(context):
        return {key: value for key, value in context.items() if key != "createdAt"}

    def _cancel_pointer_drag(self, reason):
        drag, self._pointer_drag = self._pointer_drag, None
        if drag is None:
            return
        observe = getattr(self.runtime, "observe_game_drag", None)
        if callable(observe):
            try:
                observe(dict(phase="cancel", gestureId=drag["gestureId"], context=drag["context"], reason=reason))
            except (OSError, ValueError, TypeError, KeyError) as error:
                self._trace("drag-cancel-rejected", reason=reason, diag=dict(error=str(error)))

    @Slot(object)
    def _game_pointer_event(self, event):
        if self._closing or self._closed or not isinstance(event, dict):
            return
        phase, gesture = event.get("phase"), event.get("gestureId")
        if phase == "cancel":
            if self._pointer_drag and gesture == self._pointer_drag["gestureId"]:
                self._cancel_pointer_drag(event.get("reason", "pointer-cancelled"))
            return
        sequence = event.get("sequence")
        if (phase not in {"down", "move", "up"} or type(sequence) is not int
                or sequence <= self._pointer_sequence or not self._bound
                or event.get("hwnd") != int(self._bound) or self.resources.busy
                or self.resources.pendingKind or self._pending_context
                or self.session.snapshot.get("resourceLocked")):
            return
        try:
            if self._native_windows.foreground_window() != int(self._bound):
                self._cancel_pointer_drag("foreground-lost")
                return
            self._pointer_sequence = sequence
            if phase == "down":
                self._cancel_pointer_drag("new-pointer-press")
                floor = self._game_pointer_pressed(event)
                if not isinstance(floor, dict) or floor.get("success"):
                    return
                if floor.get("reason") != "floor-click-not-a-control":
                    return  # Expired/native-geometry-rejected input cannot become X/drag.
                # observe_game_floor_click just validated this press. Runtime
                # rechecks the complete scope under its lock for drag updates;
                # a nonblocking metadata read returning None is not cancellation.
                from map_pointer_policy import cursor_is_map_close_control
                context = event["context"]
                if cursor_is_map_close_control((event["x"], event["y"]), context.get("clientBounds")):
                    self._close_map_from_input(dict(inputSequence=self._map_input_sequence,
                        pointerSequence=sequence, inputCreatedAt=event.get("createdAt"),
                        inputSource="mouse-close-control"))
                    return
                observe = getattr(self.runtime, "observe_game_drag", None)
                if not callable(observe):
                    self._trace("drag-unavailable", reason="runtime-transient-api-unavailable")
                    return
                from client_map_input import DragMotionAccumulator
                motion = DragMotionAccumulator((event["x"], event["y"]), context["clientBounds"],
                    context.get("dragBoundaryNormalized"))
                result = observe(dict(phase="down", gestureId=gesture, context=context,
                    pointerSequence=sequence, createdAt=event.get("createdAt"), delta=[0., 0.], totalDelta=[0., 0.]))
                if isinstance(result, dict) and result.get("success"):
                    self._pointer_drag = dict(gestureId=gesture, context=deepcopy(context), motion=motion, moved=False)
                return
            drag = self._pointer_drag
            if drag is None or gesture != drag["gestureId"]:
                return
            if (not isinstance(event.get("context"), dict)
                    or self._pointer_context_fields(event["context"]) != self._pointer_context_fields(drag["context"])):
                self._cancel_pointer_drag("pointer-context-changed")
                return
            motion = drag["motion"].consume(event)
            if any(motion["delta"]) and not drag["moved"]:
                self._cancel_session_frame("mouse-drag-motion")
                drag["moved"] = True
            observe = getattr(self.runtime, "observe_game_drag", None)
            if not callable(observe):
                self._cancel_pointer_drag("runtime-transient-api-unavailable")
                return
            result = observe(dict(phase=phase, gestureId=gesture, context=drag["context"],
                pointerSequence=sequence, createdAt=event.get("createdAt"),
                delta=motion["delta"], totalDelta=motion["totalDelta"]))
            if not isinstance(result, dict) or not result.get("success"):
                self._cancel_pointer_drag("runtime-drag-rejected")
                return
            if phase == "up":
                self._pointer_drag = None
            self._present_runtime_state(self.runtime.snapshot(), trigger="pointer-drag-" + phase)
            if phase == "up" and result.get("probeRequired") is True:
                # The old mouse-up requests observation only. Runtime scopes
                # this private probe and redirects later transient refreshes;
                # neither this request nor raw motion may fit/commit a pose.
                self.session.refreshRuntime()
                self.session.dispatchWithPayload("probe_map_after_drag", dict(
                    gestureId=gesture, pointerSequence=sequence, context=deepcopy(drag["context"])))
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._cancel_pointer_drag("invalid-pointer-input")
            self._trace("pointer-rejected", reason="invalid-input-context", diag=dict(error=str(error)))

    def _game_pointer_pressed(self, event):
        if (self._closed or self._closing or not isinstance(event, dict) or not self._bound
                or event.get("hwnd") != int(self._bound) or self.resources.busy
                or self.resources.pendingKind or self.session.snapshot.get("resourceLocked")
                or self._pending_context or not self.session.snapshot.get("roundActive")):
            return
        try:
            if self._native_windows.foreground_window() != int(self._bound):
                return
            result = self.runtime.observe_game_floor_click(event["x"], event["y"], event.get("context"))
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._trace("floor-click-rejected", reason="invalid-input-context", diag=dict(error=str(error)))
            return
        if not result.get("success"):
            return result
        self._invalidate_display("game-floor-control-intent")
        self._clear_reference()
        self._trace("floor-click-intent", state=self.runtime.snapshot(), reason=result.get("reason", ""),
                    diag=dict(clickedFloor=result.get("clickedFloorId"), expiresAt=result.get("expiresAt")))
        if result.get("captureRequired"):
            self.session.dispatchWithPayload("refresh_map", dict(
                floorClickSequence=event.get("sequence"), floorClickCreatedAt=event.get("createdAt")))
        return result

    def _action_requested(self, action, payload):
        if (action in {"end_round", "stop_round", "reset_round", "bind_capture"}
                or (action == "set_context" and payload.get("mode") != self._applied_context[0])):
            self._reset_map_input("action-" + action)
        if action in {"end_round", "stop_round", "reset_round", "set_floor", "cycle_floor"}:
            self._clear_reference()
        self._focus_notice = ""
        self._requested_action = action
        if action == "select_group_member":
            # Supersede an older candidate material, while retaining the current
            # surface until this explicit choice validates and is accepted.
            self._cancel_pending_material("group-choice-requested")
        state = self.session.snapshot
        if action == "select_candidate" and payload.get("index") != state.get("selectedCandidateIndex", -1):
            self._invalidate_display()
        elif action == "set_floor" and str(payload.get("floor", "")) != str(state.get("floor", "")):
            self._invalidate_display()
        elif action == "cycle_floor" and len(state.get("availableFloors", ())) > 1:
            self._invalidate_display()
        elif action == "confirm_route":
            candidate = state.get("selectedCandidate") or {}
            if not state.get("identityLocked") or candidate.get("mapId") != state.get("mapId"):
                self._invalidate_display()
        self.changed.emit()

    @Slot(str)
    def navigate(self, name):
        if name in {"home", "recognition", "gallery", "resources", "downloads", "custom", "settings", "replay", "help"}:
            self.pageRequested.emit(name)
            self.windows.show("main")
            window = self.windows._windows.get("main")
            if window is not None:
                window.showNormal()
                window.raise_()
                window.requestActivate()

    @Slot()
    def requestManualMapSelection(self):
        if self._closing or self._closed:
            return
        self.session.dispatch("reselect_map")
        self.navigate("recognition")

    def _notification_geometry(self):
        from client_screen_geometry import project_client_geometry, ScreenGeometryError
        screen = None
        diagnostics = dict(placement="primary-fallback", gameMonitorMatched=False,
                           reason="no-bound-game")
        query = getattr(self._native_windows, "monitor_info", None)
        if self._bound:
            try:
                if not callable(query):
                    raise ValueError("native-monitor-info-unavailable")
                info = query(int(self._bound))
                left, top, right, bottom = info["monitorRect"]
                projection = project_client_geometry((left, top), (right-left, bottom-top),
                    info["device"], info["monitorRect"], QGuiApplication.screens())
                screen = projection.screen
                diagnostics = dict(placement="game-monitor", gameMonitorMatched=True,
                                   projection=projection.diagnostics)
            except ScreenGeometryError as error:
                diagnostics.update(reason="screen-projection-unverified", detail=str(error),
                                   projection=error.diagnostics)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
                diagnostics.update(reason="native-monitor-unavailable", detail=str(error)[:512])
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            diagnostics.update(placement="default-fallback", fallbackReason="no-primary-screen")
            return dict(x=0, y=0, width=800, height=600, diagnostics=diagnostics)
        area = screen.availableGeometry()
        return dict(x=area.x(), y=area.y(), width=area.width(), height=area.height(), diagnostics=diagnostics)

    notificationGeometry = Property("QVariantMap", _notification_geometry, notify=changed)

    @Slot()
    def returnToGame(self):
        result = self._game_focus.requestFocus()
        self._message = str(result.get("message", ""))
        self._focus_notice = self._message
        self.changed.emit()

    @Slot()
    def startLiveServices(self):
        """Startup only in the real composition; injected probe catalogs opt out."""
        if self._closing or self._closed:
            return
        self._automatic_live = True
        if self._input_enable_allowed() and self.input.configureSettings(self.settings.persistedValues):
            enable = getattr(self.input, "enableManagement", None)
            if callable(enable):
                enable()
        self._discovery_timer.start()
        self._discover_game()

    def _input_enable_allowed(self):
        if another_assistant_running():
            self._message = "检测到另一个小抄客户端仍在运行，请先退出后再启用快捷键"
            self.changed.emit()
            return False
        return True

    @staticmethod
    def _window_identity(row):
        return (str(row["hwnd"]), row.get("pid"), row.get("processCreateTime"), row.get("executable"))

    def _clear_reference(self):
        if not self._reference and not self._edit and self._reference_preview_context is None:
            return
        self.cancelReferenceEdit()
        had_reference = bool(self._reference)
        self._reference = {}
        self.windows.hide("reference")
        if had_reference:
            clear = getattr(self.images, "clear_reference", None)
            if callable(clear):
                clear()
            elif not self._aligned:
                self.images.clear()

    def _drop_binding(self, reason):
        self._reset_map_input(reason)
        self.input.disable()
        self.capture.disable()
        self._revoke_runtime(reason)
        self._clear_reference()
        self.session.stop()
        self._bound, self._binding_identity = "", None
        self.input.bindWindow(0)
        self.session.dispatch("end_round")
        self._message = "游戏窗口已关闭，等待唯一游戏窗口后自动重新连接"
        self.notificationRequested.emit("游戏连接已断开", self._message, "warning")

    def _discover_game(self):
        if self._closing or self._closed:
            return
        if self._automatic_live and not self._input_enable_allowed():
            return
        try:
            rows = list(self._catalog())
        except Exception as error:
            # An unreadable catalog cannot authorize a replacement binding.
            self._message = f"窗口读取失败：{error}"
            self.changed.emit()
            return
        self._window_rows = rows
        identities = {self._window_identity(row) for row in rows}
        if self._bound and self._binding_identity is not None and self._binding_identity not in identities:
            self._drop_binding("game-window-lost")
        if not self._bound:
            if len(rows) == 1 and not self.session.busy and not self.resources.busy and not self.resources.pendingKind:
                self.bindGameWindow(rows[0]["hwnd"])
            elif len(rows) > 1:
                self._message = "发现多个游戏窗口，请明确选择要连接的窗口"
            elif not rows:
                self._message = "等待第五人格游戏窗口；管理快捷键保持可用"
        signature = (tuple(sorted(identities, key=str)), self._bound)
        if signature != self._last_catalog_notice:
            self._last_catalog_notice = signature
            self.changed.emit()

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
        self._reset_map_input("binding-changed")
        self.input.disable()
        self.capture.disable()
        self._revoke_runtime("binding-changed")
        self._focus_notice = ""
        self._bound = str(hwnd)
        self._binding_identity = self._window_identity(row)
        self._clear_reference()
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
            elif not self._input_enable_allowed():
                pass
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
        if (self.session.busy or self.resources.busy
                or self.settings.busy or self._pending_context):
            self._message = "请等待当前操作和模式设置完成"
            self.changed.emit(); return
        # Preparation is permitted before a game exists and does not grant
        # capture/input authority. A later G opens/starts atomically in runtime.
        if self._bound:
            self.setInputEnabled(True)
        self.windows.show("controls")
        self.session.dispatch("start_round")

    @Slot(bool)
    def setTextEntryActive(self, active):
        self._text_entry = bool(active)
        self.input.setTextEntryActive(self._text_entry or self._shortcut_recording)
        self.changed.emit()

    @Slot(bool)
    def _set_shortcut_recording(self, active):
        self._shortcut_recording = bool(active)
        self.input.setEditingBindings(self._shortcut_recording)
        self.input.setTextEntryActive(self._text_entry or self._shortcut_recording)
        self.changed.emit()

    @Slot()
    def toggleControls(self):
        self._toggle_controls("mouse")

    @Slot()
    def toggleControlsKeyboard(self):
        self._toggle_controls("keyboard")

    def _toggle_controls(self, source):
        if self._closing or self._closed or self._shortcut_recording:
            return
        before = bool(self.windows.snapshot.get("controls", {}).get("visible"))
        if before:
            self.windows.hide("controls")
        else:
            self.settings.setValue("orbVisible", True)
            self.windows.show("controls")
        after = bool(self.windows.snapshot.get("controls", {}).get("visible"))
        if before != after:
            self.controlVisibilityChanged.emit(after, source)

    @Slot(str)
    def setMode(self, mode):
        if mode not in {"nightmare", "difficult"} or self.settings.busy or self.resources.busy:
            return
        if mode != self.settings.persistedValues.get("gameMode") and self.session.snapshot.get("roundActive"):
            self._message = "请先结束本局，再切换游戏模式"
            self.notificationRequested.emit("暂不能切换模式", self._message, "warning")
            self.changed.emit()
            return
        if mode == self.settings.persistedValues.get("gameMode"):
            return
        self._clear_reference()
        self._revoke_runtime("mode-preference-changed")
        if self.settings.setValue("gameMode", mode):
            self.settings.saveFields(["gameMode"])

    @Slot(str)
    def setRouteProfile(self, profile):
        if self.settings.busy or self.resources.busy:
            return
        values = self.settings.values
        choices = dict(values.get("modeRouteProfiles", {}))
        choices[self.session.currentMode] = profile
        if self.settings.setValue("routeProfileId", profile) and self.settings.setValue("modeRouteProfiles", choices):
            self.settings.saveFields(["routeProfileId", "modeRouteProfiles"])

    def _sync_reference_payload(self, payload):
        if self._edit is not None and self._reference.get("previewOnly"):
            self._show_reference_current()
            return  # Explicit position preview never becomes a runtime grant.
        if not payload or not payload.get("referenceAuthorized"):
            self._clear_reference()
            return
        context = (payload.get("mode"), payload.get("roundId"), payload.get("mapId"),
            payload.get("floor"), payload.get("profileId"), payload.get("variantId"),
            payload.get("displayGeneration"), repr(payload.get("resourceIdentity")))
        if tuple(self._reference.get("context", ())) != context:
            self.cancelReferenceEdit()
            key = "reference:" + str(payload["token"])
            if payload.get("pixels") is None:
                self._clear_reference()
                return
            url = self.images.publish(key, payload["pixels"])
            self._reference = dict(imageUrl=url, context=context, key=key,
                mapId=payload.get("mapId"), floor=payload.get("floor"), profileId=payload.get("profileId"))
        for key in ("identityPhase", "provisional", "groupId", "durableMapId", "roundId", "cycleId", "frameId", "generation"):
            self._reference[key] = payload.get(key)
        label = "暂用地图" if payload.get("provisional") else "定点地图"
        self._reference["title"] = f"{label} · {payload.get('mapId')} · {payload.get('floor')}层"
        self._trace("reference-publish", payload=self._reference)
        self._show_reference_current()

    def _reference_focus_allowed(self):
        """Use the old game/own-editor focus rule for every presentation path."""
        try:
            foreground = self._native_windows.foreground_window()
            if self._bound and foreground == int(self._bound):
                return True
            if self._edit is not None and foreground:
                return self._native_windows.window_process_id(foreground) == os.getpid()
        except (OSError, ValueError, AttributeError):
            pass
        return False

    def _restore_reference_placement(self, preferred_size=None):
        window = self.windows._windows.get("reference")
        if window is None or (self._edit is not None and
                              (preferred_size is None or self._edit.offset is not None)):
            return
        from client_reference_presentation import restore_reference_geometry, UNKNOWN
        screens = QGuiApplication.screens() if isinstance(QGuiApplication.instance(), QGuiApplication) else []
        if not screens:
            return
        values = self.settings.values
        size = preferred_size or [window.property("preferredWidth") or window.width(),
                                  window.property("preferredHeight") or window.height()]
        position = [window.x(), window.y()] if self._edit else values.get("sheetPosition")
        space = "qt-logical-v1" if self._edit else values.get("sheetPositionSpace", UNKNOWN)
        signature = (repr(position), space,
            tuple(size), tuple((s.name(), s.geometry().getRect(), s.availableGeometry().getRect(),
                                s.devicePixelRatio()) for s in screens))
        if signature == self._reference_placement_signature:
            return
        try:
            placement = restore_reference_geometry(position, size, screens, position_space=space)
            window.setScreen(placement.screen)
            window.setProperty("restoredGeometry", dict(x=placement.x, y=placement.y,
                width=placement.width, height=placement.height))
            self._reference_placement_signature = signature
            if self._edit is not None:
                self._edit.place((placement.x, placement.y))
                self._edit.size = (placement.width, placement.height)
            self._trace("reference-placement", payload=self._reference, diag=placement.diagnostics)
        except (ValueError, RuntimeError, TypeError) as error:
            self._trace("reference-placement-rejected", payload=self._reference, reason=str(error))

    def referenceScreensChanged(self):
        self._reference_placement_signature = None
        window = self.windows._windows.get("reference")
        size = [window.property("preferredWidth") or window.width(),
                window.property("preferredHeight") or window.height()] if window else None
        self._restore_reference_placement(size)

    def _show_reference_current(self):
        allowed = self._reference_focus_allowed()
        if self._reference and (self._edit is not None or self.settings.values.get("referenceSheetVisible", True)) and allowed:
            self._restore_reference_placement()
            if self._edit is None:
                self.windows.setClickThrough("reference", True)
            if not self.windows.snapshot.get("reference", {}).get("visible"):
                self.windows.show("reference")
        else:
            self.windows.hide("reference")

    @Slot()
    def showReference(self):
        getter = getattr(self.runtime, "reference_payload", None)
        if callable(getter):
            self._sync_reference_payload(getter())
        else:
            self._show_reference_current()
        if not self._reference:
            self._message = "请先识别并确认本局地图和楼层，再显示定点地图"
            self.changed.emit()

    def _set_visibility(self, field, value):
        if self.settings.setValue(field, value):
            self.settings.saveFields([field])

    @Slot()
    def toggleAlignedVisibility(self):
        enabled = not bool(self.settings.values.get("sheetVisibilityEnabled", True))
        self._set_visibility("sheetVisibilityEnabled", enabled)
        if not enabled:
            self.windows.hide("aligned")
        else:
            self._session_changed()
            if self._aligned:
                self.windows.show("aligned")

    @Slot()
    def toggleReferenceVisibility(self):
        enabled = not bool(self.settings.values.get("referenceSheetVisible", True))
        self._set_visibility("referenceSheetVisible", enabled)
        if not enabled:
            self.hideReference()
        elif self._reference:
            self.showReference()

    @Slot()
    def hideReference(self):
        self.cancelReferenceEdit()
        self.windows.hide("reference")

    @Slot()
    def beginReferenceEdit(self):
        if self.resources.busy or self._closing or self._closed:
            return
        if not self._reference:
            provider = self._reference_preview_provider
            if provider is None:
                self._message = "尚无可预览的地图资源，请先导入图包"
                self.changed.emit()
                return
            if self._reference_preview_context is None:
                self._edit_generation += 1
                context = (*self._applied_context, self._edit_generation)
                self._reference_preview_context = context
                provider.request(*self._applied_context, context=context,
                    variant_id=self.settings.values.get("zhan10CoffinVariantId", "variant-01"))
                self._message = "正在准备位置预览，不参与本局识别"
                self.changed.emit()
            return
        window = self.windows._windows.get("reference")
        if window is None or self._edit:
            return
        self._restore_reference_placement()
        self._edit_generation += 1
        self._before_edit = (window.x(), window.y())
        self._reference_clickthrough = bool(self.windows.snapshot["reference"]["clickThrough"])
        self._edit = ReferencePositionEdit(self._edit_generation, (self._reference["key"],),
                                          self._before_edit, (window.width(), window.height()))
        self.windows.setClickThrough("reference", False)
        self._show_reference_current()
        self._message = "正在编辑定点位置；松开鼠标后仍可继续移动，点击完成保存"
        self.changed.emit()

    def setReferencePreviewProvider(self, provider):
        self._reference_preview_provider = provider
        provider.ready.connect(self._reference_preview_ready)
        provider.failed.connect(self._reference_preview_failed)

    def _reference_preview_ready(self, payload):
        context = self._reference_preview_context
        if (self._closed or self._closing or context is None
                or tuple(payload.get("context", ())) != context
                or tuple(context[:2]) != self._applied_context
                or payload.get("previewOnly") is not True
                or payload.get("referenceAuthorized") or payload.get("liveAuthorization")):
            return
        self._reference_preview_context = None
        if self.resources.busy:
            self._message = "资源操作尚未完成，请稍后重试位置预览"
            self.changed.emit()
            return
        key = "reference:preview:" + str(payload["requestId"])
        self._reference = {k: v for k, v in payload.items() if k != "pixels"}
        self._reference.update(key=key, imageUrl=self.images.publish(key, payload["pixels"]))
        self.beginReferenceEdit()

    def _reference_preview_failed(self, raw):
        if self._reference_preview_context is None or self._closing or self._closed:
            return
        self._reference_preview_context = None
        self._message = "位置预览读取失败，请检查已安装地图资源"
        self.errorReported.emit(dict(title="位置预览未完成", summary=self._message,
            raw=str(raw), phase="presentation", action="reference-preview", kind="error"))
        self.notificationRequested.emit("位置预览未完成", self._message, "error")
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
        if self._reference_preview_context is not None:
            self._reference_preview_context = None
            if self._reference_preview_provider is not None:
                self._reference_preview_provider.cancel()
        if not self._edit:
            return
        self._edit.cancel()
        self._edit = None
        window = self.windows._windows.get("reference")
        if restore and window is not None and self._before_edit:
            window.setX(self._before_edit[0]); window.setY(self._before_edit[1])
        self._before_edit = None
        self.windows.setClickThrough("reference", self._reference_clickthrough)
        if self._reference.get("previewOnly"):
            self._reference = {}
            clear = getattr(self.images, "clear_reference", None)
            if callable(clear):
                clear()
            self.windows.hide("reference")
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
        from client_reference_presentation import QT_LOGICAL
        self.settings.setValue("sheetPositionSpace", QT_LOGICAL)
        self._saving_position = self.settings.saveFields(["sheetPosition", "sheetPositionSpace"])
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
        if self._closing or self._closed:
            return
        values = self.settings.values
        if not values.get("referenceSheetVisible", True):
            self.hideReference()
        if not values.get("sheetVisibilityEnabled", True):
            self.windows.hide("aligned")
        if not values.get("orbVisible", True):
            self.windows.hide("controls")
        self.changed.emit()

    def _settings_saved(self):
        if self._closing or self._closed:
            return
        values = self.settings.persistedValues
        signature = (values.get("hotkey"), tuple(values.get("actionHotkeys", ())))
        if signature != self._binding_signature:
            was_enabled = self.input.enabled
            if self.input.configureSettings(values):
                self._binding_signature = signature
                if was_enabled and self._input_enable_allowed():
                    self.input.enable()
                enable_management = getattr(self.input, "enableManagement", None)
                if self._automatic_live and callable(enable_management) and self._input_enable_allowed():
                    enable_management()
        configure = getattr(self.runtime, "configure_settings", None)
        if configure:
            configure(values)
        mode = values.get("gameMode", "nightmare")
        profile = values.get("modeRouteProfiles", {}).get(mode) or values.get("routeProfileId", "")
        # Mode and author reconfiguration is serialized by SessionService; don't
        # race two independent commands with different generation tokens.
        if (mode, profile) != self._applied_context:
            self._cancel_pointer_drag("preferences-committed")
            if mode != self._applied_context[0]:
                self._clear_reference()
                self._revoke_runtime("preferences-committed")
            else:
                # Author selection changes pixels/bridge, never recognition
                # identity or the already verified canonical pose.
                self.cancelReferenceEdit()
                self._cancel_pending_material("route-preference-changed")
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
        if self.session.snapshot.get("roundActive") and mode != self._applied_context[0]:
            self._message = "请先结束本局，再切换游戏模式"
            return
        if self.session.reconfigure(mode, profile):
            self._context_timer.stop()
            if not self.session.busy:
                self._session_changed()
        else:
            self._message = self.session.message

    def _resources_changed(self):
        if self._closing or self._closed:
            return
        self._reset_map_input("resources-changed")
        self._invalidate_display()
        self._clear_reference()
        invalidate = getattr(self.runtime, "invalidate", None)
        if invalidate:
            invalidate("resources-changed")
        self.session.refreshRuntime()
        self.backend.refresh()
        self.changed.emit()

    def _invalidate_display(self, reason="display-invalidated"):
        self._cancel_pending_material(reason)
        self._trace("clear", payload=self._aligned, reason=reason,
                    window=dict(name="aligned", visible=False))
        self._aligned = {}
        self._last_render_token = None
        self.windows.hide("aligned")
        clear = getattr(self.images, "clear_aligned", None)
        if callable(clear):
            clear()
        elif not self._reference:
            self.images.clear()

    def _revoke_runtime(self, reason):
        invalidate = getattr(self.runtime, "invalidate", None)
        if invalidate:
            invalidate(reason)
        self._invalidate_display(reason)

    def _withdraw_expired_source(self, reason):
        """Withdraw an expired source without rejecting a current target grant.

        A manual target may fit the new frame even when the old member cannot.
        Only its runtime-validated pending grant can survive this presentation
        update. Explicit map/floor/resource/end invalidation still cancels both.
        """
        token = self._pending_aligned.get("token")
        getter = getattr(self.runtime, "pending_sibling_payload", None)
        current = getter(token) if token and callable(getter) else None
        if current and current.get("token") == token and current.get("liveAuthorization"):
            self._trace("clear", payload=self._aligned, reason=reason,
                        window=dict(name="aligned", visible=False))
            self._aligned, self._last_render_token = {}, None
            self.windows.hide("aligned")
            # Keep the bounded staged image and its original acceptance timer.
            # The source Image is cleared by alignedData, never painted stale.
            return
        self._invalidate_display(reason)

    def _session_changed(self):
        if self._closing or self._closed:
            return
        if self.session.busy:
            pending = self.session.snapshot.get("pendingAction")
            reprojecting = (pending == "set_context" and self._pending_context
                            and self._pending_context[0] == self._applied_context[0])
            if not reprojecting and pending not in {
                    "refresh_map", "reselect_map", "previous_candidate", "next_candidate",
                    "select_candidate", "confirm_route", "set_floor", "cycle_floor",
                    "select_group_member"}:
                self._invalidate_display()
            self.changed.emit(); return
        state = self.session.snapshot
        self._notify_session_transitions(state)
        actual_context = self.runtime.snapshot()
        if self._pending_context and (actual_context.get("mode"), actual_context.get("profileId")) == self._pending_context:
            self._applied_context = self._pending_context
            self._pending_context = None
            self._context_timer.stop()
        receipt = state.get("lastReceipt", {})
        if receipt.get("action") == "bind_capture":
            if receipt.get("success") and self._bound:
                self._message = "窗口已连接；切回游戏打开地图即可识别"
                if self._automatic_live and not self.input.enabled and self._input_enable_allowed():
                    self.input.enable()
            else:
                self._bound = ""
                self.input.disable()
                self._message = "连接失败，请刷新游戏窗口后重试"
        self._present_runtime_state(state, trigger="session-completed")

    def _present_runtime_state(self, state, *, trigger):
        """Present only a current runtime grant; never alter Session job context.

        A group-review worker can publish before its command completes. Copying
        its new frameId into Session's transaction state here would stale that
        very command, so presentation consumes immutable payloads separately.
        """
        reference_getter = getattr(self.runtime, "reference_payload", None)
        if callable(reference_getter):
            self._sync_reference_payload(reference_getter())
        render = state.get("render") or {}
        token = render.get("token") or state.get("renderToken")
        get_payload = getattr(self.runtime, "render_payload", None)
        if not token or not render.get("liveAuthorization"):
            # A pending identity review may retain a separately verified frame.
            # The runtime getter, not the old view, decides if it is still valid.
            retained = get_payload(self._last_render_token) if get_payload and self._last_render_token else None
            if retained and retained.get("liveAuthorization"):
                token, render = retained["token"], retained
                self._trace("retain-current", payload=retained, reason="current-grant-during-review")
            else:
                self._trace("drop", state=state, reason="no-current-live-grant", diag=dict(trigger=trigger))
                self._withdraw_expired_source("runtime-grant-unavailable")
                self.changed.emit(); return
        if token == self._last_render_token:
            self.showReference()
            current = get_payload(token) if get_payload else None
            if current and current.get("liveAuthorization"):
                scope = ("epoch", "mode", "profileId", "mapId", "floor", "roundId", "cycleId",
                         "captureGeneration", "displayGeneration", "variantId",
                         "resourceIdentity", "routeWidth", "routeHeight", "clientWidth", "clientHeight")
                if any(current.get(key) != self._aligned.get(key) for key in scope):
                    self._invalidate_display("render-transform-scope-changed")
                    self.changed.emit()
                    return
                changed = False
                # A verified unchanged-pose refresh retains the material token,
                # but renews frame authority. Consume that current grant before
                # a subsequent pointer transform; never reload its pixels.
                frame_metadata = {key: current.get(key) for key in
                                  ("authorizationId", "frameId", "generation", "observedFloor", "floorSource")}
                if any(self._aligned.get(key) != value for key, value in frame_metadata.items()):
                    self._aligned.update(frame_metadata)
                    changed = True
                revision = current.get("transformRevision", 0)
                if (type(revision) is int and revision > self._aligned.get("transformRevision", 0)):
                    # The runtime owns this current-frame transient transform.
                    # Keep material/URL and capture-to-DIP projection unchanged.
                    for key in ("matrix", "transformRevision", "transient", "transientOffset", "persistentPoseUpdated"):
                        if key in current:
                            self._aligned[key] = deepcopy(current[key])
                    self._trace("publish-transform", payload=self._aligned, reason=trigger)
                    changed = True
                metadata = {key: current.get(key) for key in
                            ("identityPhase", "provisional", "groupId", "durableMapId")}
                if any(self._aligned.get(key) != value for key, value in metadata.items()):
                    self._aligned.update(metadata)
                    self._trace("publish-metadata", payload=self._aligned, reason=trigger)
                    changed = True
                if changed:
                    self.changed.emit()
                self._notify_first_valid_output(self._aligned)
            if not self.settings.values.get("sheetVisibilityEnabled", True):
                self.windows.hide("aligned")
                self._trace("hide", payload=self._aligned, reason="preference-disabled",
                            window=dict(name="aligned", visible=False))
            elif current and current.get("liveAuthorization"):
                self.windows.show("aligned")
            return
        payload = get_payload(token) if get_payload else None
        if not payload or not payload.get("liveAuthorization"):
            self._trace("drop", state=state, reason="payload-expired", diag=dict(trigger=trigger))
            self._withdraw_expired_source("runtime-payload-expired")
            self.changed.emit(); return
        public = {key: value for key, value in payload.items() if key != "pixels"}
        try:
            self._convert_client_geometry(public)
        except (OSError, ValueError) as error:
            self._message = f"暂不显示贴合层：{error}"
            self._trace("drop", payload=payload, reason="window-geometry-unverified", diag=dict(error=str(error)))
            self._report_display_failure(payload, "window-geometry-unverified", _presentation_error_details(error))
            self._invalidate_display("window-geometry-unverified")
            self.changed.emit(); return
        path = public.pop("routePath", None)
        if self._accepted_material.get("token") == token:
            public["routeUrl"] = self._accepted_material["routeUrl"]
        elif path:
            public["routeUrl"] = QUrl.fromLocalFile(str(path)).toString()
        elif payload.get("pixels") is not None:
            public["routeUrl"] = self.images.publish(str(token), payload["pixels"],
                                                    material_scope=_aligned_material_scope(payload))
        else:
            self._trace("drop", payload=payload, reason="route-material-unavailable")
            self._report_display_failure(payload, "route-material-unavailable", "Runtime returned no route path or pixels")
            self._invalidate_display("route-material-unavailable"); return
        self._aligned, self._last_render_token = public, token
        self._accepted_material = {}
        self._trace("publish", payload=public, reason=trigger,
                    window=dict(name="aligned", visible=bool(self.settings.values.get("sheetVisibilityEnabled", True)),
                                opacity=self.settings.values.get("sheetOpacity")))
        if not callable(reference_getter):
            # Compatibility for explicitly injected older runtime fixtures only;
            # the production runtime has independent reference authorization.
            self._sync_reference_payload(dict(payload, referenceAuthorized=True))
        self._notify_first_valid_output(public)
        self.changed.emit()
        if self.settings.values.get("sheetVisibilityEnabled", True):
            self.windows.setClickThrough("aligned", True)
            self.windows.show("aligned")
        else:
            self.windows.hide("aligned")
            self._trace("hide", payload=public, reason="preference-disabled",
                        window=dict(name="aligned", visible=False))

    def _notify_first_valid_output(self, public):
        if public.get("provisional") or public.get("identityPhase") in {"UNLOCKED", "GROUP_LOCKED"}:
            return
        success_key = ("success", public.get("roundId"), public.get("mapId"))
        if success_key not in self._notified:
            self._notified.add(success_key)
            self.notificationRequested.emit("地图已识别", f"{public.get('mapId')} · {public.get('floor')}层", "success")

    def _report_display_failure(self, payload, reason, raw):
        key = ("display-failure", payload.get("roundId"), payload.get("cycleId"),
               payload.get("mapId"), payload.get("floor"), reason)
        if key in self._notified:
            return
        self._notified.add(key)
        summary = "贴合结果暂时无法显示，窗口或图像信息未能核对；详情已保存在帮助页的“最近问题”。"
        self.errorReported.emit(dict(title="贴合显示未完成", summary=summary, raw=raw,
            phase="presentation", action="publish", kind="error", mapId=payload.get("mapId"), floor=payload.get("floor")))
        self.notificationRequested.emit("贴合显示未完成", summary, "error")

    def _input_changed(self):
        if self._closed or self._closing:
            return
        state = getattr(self.input, "snapshot", {})
        pointer_error = state.get("floorClickObserverError")
        pointer_key = ("floor-click-observer", pointer_error)
        if pointer_error and pointer_key not in self._notified:
            self._notified.add(pointer_key)
            summary = "游戏内楼层按钮的点击监听暂不可用，自动楼层检测和快捷键仍独立运行；详情可在最近问题查看。"
            self.errorReported.emit(dict(title="楼层点击监听暂不可用", summary=summary,
                raw=pointer_error, action="game-floor-control", phase="input", kind="warning"))
            self.notificationRequested.emit("楼层点击监听暂不可用", summary, "warning")
        if state.get("status") in {"failed", "conflict", "unknown"}:
            key = ("input-failure", state.get("message"))
            if key not in self._notified:
                self._notified.add(key)
                summary = "全局快捷键暂不可用，原始原因已保存在帮助页的“最近问题”。"
                self.errorReported.emit(dict(title="快捷键暂不可用", summary=summary,
                    raw=state.get("message", ""), action="native-input", phase="input", kind="warning"))
                self.notificationRequested.emit("快捷键暂不可用", summary, "warning")

    def _notify_session_transitions(self, state):
        current = self.runtime.snapshot()
        active = bool(current.get("roundActive", state.get("roundActive")))
        receipt = state.get("lastReceipt", {})
        prepared = (current.get("mode", state.get("mode")), current.get("roundId", state.get("roundId")))
        preparing = bool(current.get("roundPreparing", state.get("roundPreparing")))
        if (preparing and not active
                and prepared == (state.get("mode"), state.get("roundId"))
                and receipt.get("action") == "start_round" and receipt.get("success")
                and prepared != self._last_prepared_round):
            self._last_prepared_round = prepared
            self.notificationRequested.emit("本局已准备", "打开游戏地图后开始识别", "info")
        if (self._last_round_preparing and not preparing and not active
                and receipt.get("action") in {"end_round", "stop_round"} and receipt.get("success")):
            self.notificationRequested.emit("已取消准备", "本局识别尚未启动", "info")
        self._last_round_preparing = preparing
        if active != self._last_round_active:
            self._last_round_active = active
            if active:
                self._notified.clear()
                self.notificationRequested.emit("开始本局", "已开始本局，打开游戏地图后自动识别", "info")
            else:
                self._fallback_notices.end_round()
                self._clear_reference()
                self.notificationRequested.emit("本局已结束", "本局显示与识别已停止", "info")
        receipt = state.get("lastReceipt", {})
        reason = receipt.get("reason", "")
        if receipt.get("success") is False:
            key = ("failure", state.get("roundId"), state.get("cycleId"), receipt.get("action"), reason)
            if key not in self._notified:
                self._notified.add(key)
                problem = operation_problem(receipt, state)
                self.errorReported.emit(problem)
                pose = state.get("poseResult") or {}
                meaningful_pose_failure = pose.get("status") == "rejected" and not state.get("displayAuthorized")
                if receipt.get("action") not in {"refresh_map", "inspect_engine_resources"} or meaningful_pose_failure:
                    self.notificationRequested.emit(problem["title"], problem["summary"], problem["kind"])
    def _native_action(self, action):
        if self._closing or self._closed:
            return
        if action == "toggle-panel":
            self.toggleControlsKeyboard()
            return
        if action == "toggle-sheet-visibility":
            self.toggleAlignedVisibility()
            return
        aliases = {"map": "map", "escape": "close_map", "candidate-prev": "previous_candidate",
                   "candidate-next": "next_candidate", "confirm-round": "confirm_route",
                   "cycle-floor": "cycle_floor", "end-round": "end_round", "start-round": "start_round",
                   "toggle-round": "toggle_round", "toggle-sheet-visibility": "toggle_sheet_visibility"}
        command = aliases.get(action)
        if command:
            if command == "close_map":
                self._close_map_from_input(dict(inputSequence=self._map_input_sequence, inputSource="escape"))
                return
            if command == "toggle_round":
                if not (self.session.snapshot.get("roundActive") or self.session.snapshot.get("roundPreparing")):
                    self.startRound()
                    return
                command = "end_round"
            # G's visual map settles on the producer's existing prepare loop,
            # not by reading a frozen frame on the UI thread.
            if command == "map":
                self._invalidate_display()
            elif command in {"capture_now", "close_map", "end_round", "toggle_round"}:
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
        from client_screen_geometry import project_client_geometry
        projection = project_client_geometry((x, y), (width, height), info.szDevice,
            (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom),
            QGuiApplication.screens())
        payload["deviceScale"] = projection.device_scale
        payload["originX"], payload["originY"] = projection.origin_x, projection.origin_y
        self._trace("geometry-verified", payload=payload, window=dict(name="aligned",
            origin=[projection.origin_x, projection.origin_y], deviceScale=projection.device_scale,
            screen=projection.diagnostics["selectedQtScreen"]), diag=projection.diagnostics)
        window = self.windows._windows.get("aligned")
        if window is not None:
            window.setScreen(projection.screen)

    def _watch_foreground(self):
        if self._closed or self._closing:
            return
        if self._edit is not None:
            self._show_reference_current()
        state = self.session.snapshot
        pending = state.get("pendingAction") if self.session.busy else ""
        if pending in {"end_round", "stop_round", "reset_round", "bind_capture", "close_map", "toggle_round"}:
            # Session's applied snapshot can still describe the previous round
            # while this terminal/rebinding job drains. It cannot re-arm an
            # obsolete return plan, even across repeated background ticks.
            self._clear_focus_return_plan()
            return
        actual = self.runtime.snapshot()
        active = actual.get("roundActive", state.get("roundActive"))
        if not self._bound or not active:
            self._clear_focus_return_plan()
            return
        try:
            foreground = self._native_windows.foreground_window()
            if foreground != int(self._bound or 0):
                first_loss = not self._waiting_foreground
                self._waiting_foreground = True
                self._map_was_open = self._map_was_open or actual.get("mapOpen", state.get("mapOpen")) or (self.session.busy and self._requested_action in {"map", "capture_now"})
                if first_loss:
                    self._trace("foreground-change", state=actual, reason="game-lost-foreground",
                                window=self._foreground_details(foreground))
                    # Revoke before the worker can finish; returning to the same
                    # HWND/size does not make a pre-focus-loss frame current.
                    self._revoke_runtime("foreground-lost")
                    self._focus_loss_map_sequence = self._map_input_sequence
                    self._reset_map_input("foreground-lost")
                    cancel = getattr(self.session, "cancelPending", None)
                    if callable(cancel):
                        cancel("foreground-lost")
                if not self._reference_focus_allowed():
                    self.windows.hide("reference")
                    self._trace("hide", payload=self._reference, reason="game-not-foreground",
                                window=dict(name="reference", visible=False))
            elif self._waiting_foreground:
                if self.session.busy:
                    return
                self._waiting_foreground = False
                self._trace("foreground-change", state=actual, reason="game-regained-foreground",
                            window=self._foreground_details(foreground))
                if self._focus_loss_map_sequence == self._map_input_sequence:
                    # Restore intent only at this synchronous return boundary,
                    # never from a later snapshot/receipt after another press.
                    reset = getattr(self.input, "resetMapInput", None)
                    if callable(reset):
                        reset(bool(self._map_was_open))
                self._focus_loss_map_sequence = None
                if self._map_was_open:
                    self._map_was_open = False
                    self._invalidate_display("foreground-return-recapture")
                    self.session.dispatch("capture_now")
                else:
                    self.showReference()
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

    def _foreground_details(self, foreground):
        """Read identifiers only; never titles, foreign paths or activation."""
        details = dict(name="foreground", expectedHwnd=str(self._bound), observedHwnd=str(foreground),
                       ownerRole="bound-game" if foreground == int(self._bound or 0) else "other")
        process_id = getattr(self._native_windows, "window_process_id", None)
        if callable(process_id) and foreground:
            try:
                details["observedPid"] = process_id(foreground)
            except OSError:
                details["observedPid"] = None
        for name, window in self.windows._windows.items():
            try:
                if window.isVisible() and int(window.winId()) == foreground:
                    details["ownerRole"] = "assistant:" + name
                    break
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        return details

    @Slot(object)
    def _on_runtime_event(self, event):
        if self._closed or self._closing or not isinstance(event, dict):
            return
        name = event.get("event", "unknown")
        payload, decision = event, {}
        if name == "group-state":
            identity = event.get("identity") or {}
            payload = dict(event, **identity)
            decision = dict(decision=dict(rankVerdict=event.get("rankVerdict", {}),
                                          groupEvidence=event.get("groupEvidence", {})))
        self._trace(name, payload=payload, reason=event.get("reason", ""),
                    role="runtime", status=event.get("status"),
                    diag={key: event.get(key) for key in ("poseResult", "poseAttempt", "groupMemberIds") if key in event},
                    **decision)
        if name == "round-started":
            # This event is emitted only after capture activation succeeds,
            # before the first recognition/fit worker can finish.
            self._notify_session_transitions(self.runtime.snapshot())
        elif name == "sibling-material-pending":
            if self._pending_context:
                self._trace("drop-event", payload=event, reason="presentation-context-pending")
                return
            self._prepare_sibling_material(event)
        elif name == "render-material-accepted":
            enqueue = getattr(self.session, "dispatchMaintenance", None)
            if callable(enqueue):
                enqueue("remember_render_material", {"renderToken": event.get("token")})
        elif name in {"sibling-rolled-back", "sibling-committed"}:
            if self._pending_aligned and event.get("token") == self._pending_aligned.get("token"):
                finish = getattr(self.images, "finish_staging", None)
                if callable(finish):
                    finish(event["token"], name == "sibling-committed")
                self._pending_aligned = {}
                self._material_timer.stop()
                self.changed.emit()
        elif name in {"render-ready", "group-state", "pose-result", "identity-committed"}:
            state = self.runtime.snapshot()
            context = (state.get("mode", self._applied_context[0]), state.get("profileId", self._applied_context[1]))
            if self._pending_context or context != self._applied_context:
                self._trace("drop-event", payload=event, reason="presentation-context-pending")
                return
            # The worker can have a valid group pose while its identity review
            # continues. Present it without changing Session's request/frame ID.
            self._present_runtime_state(state, trigger=name)
        elif name == "render-revoked":
            getter = getattr(self.runtime, "render_payload", None)
            if getter is None or not self._last_render_token or getter(self._last_render_token) is None:
                self._withdraw_expired_source(event.get("reason") or "render-revoked")
                self.changed.emit()
        elif name == "aligned-visibility-changed":
            if self.runtime.snapshot().get("alignedVisible") is False:
                self.windows.hide("aligned")
                self._trace("hide", payload=self._aligned, reason="preference-disabled",
                            window=dict(name="aligned", visible=False))
                self.changed.emit()

    def _track_map(self):
        if (self._closed or self._closing or not self._bound or self.session.busy
                or self.resources.busy or self.resources.pendingKind or self._pointer_drag is not None):
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
        self._reset_map_input("client-close-requested")
        self.lifecycleEvent.emit(dict(stage="close-requested", closing=True, closeReady=False))
        self._discovery_timer.stop()
        self._startup_timer.stop()
        self._foreground_timer.stop()
        self._tracking_timer.stop()
        self._context_timer.stop()
        shutdown_input = getattr(self.input, "shutdown", None)
        self._material_timer.stop()
        if callable(shutdown_input):
            shutdown_input()
        else:
            self.input.disable()  # Explicitly injected pre-hook fixture adapter.
        self.capture.disable()
        self._revoke_runtime("client-close-requested")
        self.session.stop()
        if self.resources.pendingKind:
            self.resources.confirmOperation(False)
        if self.resources.canCancel:
            self.resources.cancel()
        for writer in self.close_writers:
            cancel = getattr(writer, "cancel", None)
            if callable(cancel):
                cancel()
            state = getattr(writer, "snapshot", {})
            if isinstance(state, dict) and state.get("pendingKind") and hasattr(writer, "confirmOperation"):
                writer.confirmOperation(False)
        self.changed.emit()
        self._close_timer.start()
        self._finish_close()

    def _finish_close(self):
        if self._close_ready or self._closed:
            return
        pools = dict(session=self.session._pool.activeThreadCount(),
                     resources=self.resources.pool.activeThreadCount(), settings=self.settings.pool.activeThreadCount())
        for index, writer in enumerate(self.close_writers):
            pool = getattr(writer, "pool", getattr(writer, "_pool", None))
            if pool is not None:
                pools[type(writer).__name__ + str(index)] = pool.activeThreadCount()
        hook_running = bool(getattr(self.input, "snapshot", {}).get("hookRunning", False))
        writes = any(bool(getattr(writer, "pendingWrites", False)) for writer in self.close_writers)
        wait_state = (tuple(pools.values()), hook_running, writes)
        if wait_state != self._last_close_wait:
            self._last_close_wait = wait_state
            self.lifecycleEvent.emit(dict(stage="close-draining", pools=pools,
                                         hookRunning=hook_running, pendingWrites=writes))
        if getattr(self.input, "snapshot", {}).get("hookRunning", False):
            return
        if (self.resources.pool.activeThreadCount() or self.settings.pool.activeThreadCount()
                or self.session._pool.activeThreadCount()):
            return
        for writer in self.close_writers:
            if getattr(writer, "pendingWrites", False):
                return
            pool = getattr(writer, "pool", getattr(writer, "_pool", None))
            if pool is not None and pool.activeThreadCount():
                return
        self._close_timer.stop()
        self._close_ready = True
        self.changed.emit()
        # Close vetoes belong to normal window interaction, not a completed
        # application shutdown. Hide all owned surfaces before requesting quit.
        shutdown_windows = getattr(self.windows, "shutdown", None)
        if callable(shutdown_windows):
            shutdown_windows()
        self.lifecycleEvent.emit(dict(stage="close-ready", closing=True, closeReady=True, hookRunning=False))
        self.quitRequested.emit()

    def shutdown(self):
        self._closed = True
        self._discovery_timer.stop()
        self._startup_timer.stop()
        self._close_timer.stop()
        self._foreground_timer.stop()
        self._context_timer.stop()
        self._tracking_timer.stop()
        callback = getattr(self.runtime, "set_event_callback", None)
        if callback:
            callback(None)
        self.input.shutdown()
        self.capture.shutdown()
        self._game_focus.shutdown()
        self.cancelReferenceEdit()
        self._invalidate_display()
