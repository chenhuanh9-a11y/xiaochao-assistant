"""Explicitly armed Qt adapter for the existing single-owner chest key service."""
from __future__ import annotations

from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from client_paths import APP_ROOT, _no_links
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from chest_auto_tap import ChestAutoTap, TAP_KEYS, TRIGGER_KEYS
from game_window_focus import NativeGameFocusAPI
from hotkey_bindings import parse_chord
from hotkey_keys import _primary_hotkey_to_vk

ACTION_LABELS = {
    "map": "游戏地图键", "escape": "关闭游戏地图", "toggle-panel": "显示/隐藏面板",
    "candidate-prev": "上一个候选", "candidate-next": "下一个候选",
    "confirm-round": "确认本局", "cycle-floor": "切换楼层", "end-round": "结束本局",
    "toggle-sheet-visibility": "显示/隐藏小抄", "start-round": "开始本局",
    "toggle-round": "开始/结束本局",
}


class ChestAutoTapService(QObject):
    """Loading an enabled preference never arms or starts the native worker.

    Qt snapshots are read only on the UI thread. The existing worker receives
    an immutable, lock-protected target, and retains ownership of key releases.
    """
    changed = Signal()

    def __init__(self, paths, coordinator, native_input, settings, *,
                 service_factory=ChestAutoTap, native=None, parent=None):
        super().__init__(parent)
        self.coordinator, self.input, self.settings = coordinator, native_input, settings
        self._native = native if native is not None else NativeGameFocusAPI()
        self._lock = threading.Lock()
        self._target = 0
        self._reserved = {}
        self._armed = self._running = self._closed = False
        self._bound = ""
        self._message = "请连接游戏并启用快捷键，再手动开启开箱连点"
        self._last = None
        path = Path(paths.settings_path).with_name("chest-auto-tap.json")
        _no_links(path)
        self._refresh_reserved()
        self._service = service_factory(path, target_window=self._target_window,
                                        reserved=lambda: dict(self._reserved))
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.pollOnce)
        coordinator.changed.connect(self.pollOnce)
        native_input.changed.connect(self.pollOnce)
        settings.saved.connect(self._settings_saved)

    def _target_window(self):
        with self._lock:
            return self._target or None

    def _refresh_reserved(self):
        # Consume the actual compiled observer bindings, including fixed Escape.
        bindings = self.input.snapshot.get("bindings", {})
        reserved = {}
        for name, text in bindings.items():
            vk = 0x1b if name == "escape" else parse_chord(text, _primary_hotkey_to_vk).vk
            if vk:
                reserved[vk] = "星图快捷键 " + ACTION_LABELS.get(str(name), "其他功能")
        changed = reserved != self._reserved
        self._reserved = reserved
        return changed

    def _snapshot(self):
        state = self._service.snapshot()
        return dict(state, savedEnabled=bool(state.get("enabled")), enabled=self._armed,
                    running=self._running, message=self._message,
                    tapKeys=list(TAP_KEYS), triggerKeys=list(TRIGGER_KEYS))

    snapshot = Property("QVariantMap", _snapshot, notify=changed)

    def _publish(self):
        state = self._snapshot()
        if state != self._last:
            self._last = state
            self.changed.emit()

    def _stop(self):
        with self._lock:
            self._target = 0
        if self._running:
            self._service.stop()
        self._running = False

    def _reconcile_reserved(self):
        previous_error = self._service.snapshot().get("error", "")
        result = self._service.reconcile_reserved()
        if not result.get("success"):
            self._armed = False
            self._timer.stop()
            self._stop()
            self._message = result.get("message", "快捷键冲突，开箱连点已暂停")
        elif previous_error:
            current_error = self._service.snapshot().get("error", "")
            if current_error != previous_error:
                self._message = current_error or "按键冲突已解除，请重新手动开启开箱连点"

    def _gate(self):
        owner, state = self.coordinator, self.input.snapshot
        hwnd = str(owner.boundWindow or "")
        if (self._closed or owner.closing or not hwnd or not state.get("enabled")
                or state.get("closed") or str(state.get("hwnd")) != hwnd):
            return 0, "请连接游戏并启用快捷键"
        if owner.textEntryActive or state.get("textEntryActive") or state.get("editingBindings"):
            return 0, "输入文字或录制快捷键时暂停"
        row = next((r for r in owner.gameWindows if str(r.get("hwnd")) == hwnd), None)
        if not row or not row.get("pid"):
            return 0, "游戏窗口绑定尚未核验，请重新连接"
        try:
            handle = int(hwnd)
            user = self._native.get_user32()
            pid = self._native.window_process_id(handle)
            if (not user.IsWindow(handle) or pid != int(row["pid"])
                    or not self._native.identity_is_allowed(self._native.window_text(handle),
                            self._native.process_image_path(pid))):
                return 0, "游戏窗口身份已变化，请重新连接"
            if int(user.GetForegroundWindow() or 0) != handle:
                return 0, "已准备；切回游戏后按住触发键，离开游戏立即暂停"
            return handle, ""
        except (OSError, ValueError, TypeError, AttributeError):
            return 0, "无法核验游戏窗口，开箱连点暂停"

    @Slot()
    def pollOnce(self):
        if self._closed:
            return
        if self.coordinator.closing:
            self.shutdown()
            return
        # Rebind/recovery can replace compiled keys without a settings.saved
        # signal. Validate the new reservation before any native worker resumes.
        if self._refresh_reserved():
            self._reconcile_reserved()
        if not self._armed:
            self._stop()
            self._publish()
            return
        if str(self.coordinator.boundWindow) != self._bound:
            self._armed = False
            self._timer.stop()
            self._stop()
            self._message = "游戏绑定已变化；请重新手动开启"
            self._publish()
            return
        handle, reason = self._gate()
        if not handle:
            self._stop()
            self._message = reason
        elif not self._running:
            with self._lock:
                self._target = handle
            result = self._service.update(dict(enabled=True))
            self._running = bool(result.get("success") and self._service.snapshot().get("enabled"))
            self._message = result.get("message", "")
            if not self._running:
                self._armed = False
                self._timer.stop()
                self._stop()
        else:
            current = self._service.snapshot()
            if not current.get("enabled") or current.get("error"):
                self._armed = False
                self._timer.stop()
                self._stop()
                self._message = current.get("error") or current.get("status", "开箱连点已暂停")
        self._publish()

    @Slot(bool, str, str, str, result="QVariantMap")
    def update(self, enabled, hold_key, tap_key, interval):
        if self._closed:
            return dict(success=False, message="客户端正在关闭")
        self._stop()
        self._armed = False
        self._timer.stop()
        self._refresh_reserved()
        # Save/validate while suspended. update(enabled=True) owns native sync,
        # so it is called only by pollOnce after the complete foreground gate.
        result = self._service.update(dict(enabled=False, holdKey=hold_key,
                                          tapKey=tap_key, intervalMs=interval))
        self._message = result.get("message", "")
        if result.get("success") and enabled:
            state = self.input.snapshot
            if (self.coordinator.boundWindow and state.get("enabled")
                    and str(state.get("hwnd")) == str(self.coordinator.boundWindow)):
                self._armed = True
                self._bound = str(self.coordinator.boundWindow)
                self._timer.start()
                self.pollOnce()
            else:
                result = dict(success=False, message="请先连接游戏并启用快捷键，再开启开箱连点")
                self._message = result["message"]
        self._publish()
        return result

    @Slot()
    def _settings_saved(self):
        if self._closed:
            return
        self._refresh_reserved()
        self._reconcile_reserved()
        self.pollOnce()

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._armed = False
        self._timer.stop()
        self._stop()
        self._service.stop()
        self._message = "开箱连点已停止"
        self._publish()
