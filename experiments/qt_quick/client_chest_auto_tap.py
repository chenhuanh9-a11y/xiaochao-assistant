"""Explicitly armed Qt adapter for the existing single-owner chest key service."""
from __future__ import annotations

from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from client_paths import APP_ROOT, _no_links
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from chest_auto_tap import TAP_KEYS, TRIGGER_KEYS, TRIGGER_VKS, key_down
from client_chest_preferences import QtChestAutoTap, INTENT_SCHEMA, read_object, merge_save
from game_window_focus import NativeGameFocusAPI
from hotkey_bindings import parse_chord
from hotkey_keys import _primary_hotkey_to_vk
from client_problem_messages import chest_message


class ChestAutoTapService(QObject):
    """Only the Qt-specific explicit opt-in restores readiness, never input.

    Qt snapshots are read only on the UI thread. The existing worker receives
    an immutable, lock-protected target, and retains ownership of key releases.
    """
    changed = Signal()
    errorReported = Signal(object)

    def __init__(self, paths, coordinator, native_input, settings, *,
                 service_factory=QtChestAutoTap, native=None, key_is_down=key_down, parent=None):
        super().__init__(parent)
        self.coordinator, self.input, self.settings = coordinator, native_input, settings
        self._native = native if native is not None else NativeGameFocusAPI()
        self._lock = threading.Lock()
        self._target = 0
        self._reserved = {}
        self._armed = self._running = self._closed = False
        self._bound = ""
        self._bound_identity = None
        self._key_is_down = key_is_down
        self._needs_release = True
        self._saved_enabled = False
        self._waiting_for_round = False
        self._ended_round = None
        self._next_round_generation = None
        self._message = "请连接游戏并启用快捷键，再手动开启开箱连点"
        self._last = None
        self._reported_error = ""
        path = Path(paths.settings_path).with_name("chest-auto-tap.json")
        _no_links(path)
        self._intent_path = path.with_name("qt-chest-intent.json")
        try:
            intent = read_object(self._intent_path, INTENT_SCHEMA)
            self._saved_enabled = intent.get("enabled") is True
        except OSError:
            self._message = "Qt 开箱偏好无法读取，已安全停用；原文件保留"
        self._refresh_reserved()
        self._service = service_factory(path, target_window=self._target_window,
                                        reserved=lambda: dict(self._reserved))
        self._service.stop()  # Startup always suspends the native owner, even with saved opt-in.
        self._armed = self._saved_enabled
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.pollOnce)
        coordinator.changed.connect(self.pollOnce)
        native_input.changed.connect(self.pollOnce)
        settings.saved.connect(self._settings_saved)
        session = getattr(coordinator, "session", None)
        if session is not None:
            session.actionRequested.connect(self._action_requested)
            session.changed.connect(self.pollOnce)
        if self._armed:
            self._message = "已记住开启偏好；等待游戏，松开后再按触发键"
            self._timer.start()

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
                reserved[vk] = "星图快捷键 " + str(name)
        changed = self._reserved != reserved
        self._reserved = reserved
        return changed

    def _snapshot(self):
        state = self._service.snapshot()
        display_state = dict(state, error=state.get("runtimeError", state.get("error", "")))
        return dict(state, savedEnabled=self._saved_enabled, enabled=self._armed,
                    running=self._running, message=chest_message(display_state, self._message),
                    waitingForRound=self._waiting_for_round and self._armed,
                    needsManualResume=self._saved_enabled and not self._armed and not self._closed,
                    status=chest_message(display_state, state.get("status", "")),
                    rawMessage=self._message, rawStatus=state.get("status", ""), rawError=state.get("error", ""),
                    tapKeys=list(TAP_KEYS), triggerKeys=list(TRIGGER_KEYS))

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    pendingWrites = Property(bool, lambda s: bool(getattr(s._service, "_thread", None)
                                                and s._service._thread.is_alive()), notify=changed)

    def _publish(self):
        state = self._snapshot()
        raw_error = state.get("rawError", "")
        if not raw_error:
            self._reported_error = ""
        if raw_error and raw_error != self._reported_error:
            self._reported_error = raw_error
            self.errorReported.emit(dict(title="开箱连点已停止", summary=state["message"],
                raw=raw_error, action="chest-auto-tap", phase="input", kind="error"))
        if state != self._last:
            self._last = state
            self.changed.emit()

    def _stop(self):
        with self._lock:
            self._target = 0
        if self._running:
            self._service.stop()
        self._running = False
        self._needs_release = True

    @Slot(str, dict)
    def _action_requested(self, action, payload):
        if self._closed:
            return
        state = getattr(getattr(self.coordinator, "session", None), "snapshot", {})
        ending = action == "toggle_round" and (state.get("roundActive") or state.get("roundPreparing"))
        if action in {"end_round", "stop_round", "reset_round"} or ending:
            # Stop at accepted intent, before the end worker or its receipt.
            # Keep the user's preference and arming; only a proven NEW round
            # may remove this latch, never the still-active previous snapshot.
            self._waiting_for_round = True
            self._ended_round = state.get("roundId")
            self._next_round_generation = None
            self._stop()
            if self._armed:
                self._message = "开箱连点保持开启；本局已结束，下一局开始后自动恢复"
            self._publish()
        elif self._waiting_for_round and action in {
                "start_round", "toggle_round", "capture_now", "prepare_manual_alignment"}:
            # A late pre-end worker must not count as the user's next round.
            # This is only intent; the validated committed snapshot below is
            # still required. Cancellation/supersession changes this generation,
            # so a late worker followed by refreshRuntime cannot restore input.
            self._next_round_generation = state.get("generation")

    def _next_round_ready(self):
        if not self._waiting_for_round:
            return True
        state = getattr(getattr(self.coordinator, "session", None), "snapshot", {})
        round_id = state.get("roundId")
        # LiveSessionRuntime increments roundId only after preparation commits.
        # SessionService publishes validated results; do not use a queued start,
        # a failed snapshot's retained lifecycle fields or a stale active flag.
        if (type(self._next_round_generation) is not int
                or state.get("generation") != self._next_round_generation
                or type(self._ended_round) is not int or type(round_id) is not int
                or round_id <= self._ended_round or not state.get("engineConnected")
                or state.get("busy") or state.get("closed") or state.get("contextPending")
                or state.get("resourceLocked")
                or not (state.get("roundActive") or state.get("roundPreparing"))):
            return False
        self._waiting_for_round = False
        return True

    def _binding_identity(self):
        hwnd = str(self.coordinator.boundWindow or "")
        row = next((r for r in self.coordinator.gameWindows if str(r.get("hwnd")) == hwnd), None)
        return ((hwnd, row.get("pid"), row.get("processCreateTime"), row.get("executable"))
                if row is not None else None)

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
        if self._refresh_reserved():
            result = self._service.reconcile_reserved()
            if not result.get("success"):
                self._armed = False
                self._timer.stop()
                self._stop()
                self._message = result.get("message", "快捷键冲突，开箱连点已暂停")
        if not self._armed:
            self._stop()
            self._publish()
            return
        bound = str(self.coordinator.boundWindow or "")
        catalog_has_bound = any(str(row.get("hwnd")) == bound
                                for row in self.coordinator.gameWindows)
        # Discovery can temporarily omit a minimized/unreadable game while the
        # coordinator retains its binding. Absence is not a replacement: the
        # foreground gate below still blocks input until a verified row returns.
        if self._bound and (bound != self._bound or
                (catalog_has_bound and self._bound_identity is not None
                 and self._binding_identity() != self._bound_identity)):
            self._armed = False
            self._timer.stop()
            self._stop()
            self._message = "游戏绑定已变化；核对游戏连接后点击“重新就绪”"
            self._publish()
            return
        if not self._bound and self.coordinator.boundWindow:
            self._bound = str(self.coordinator.boundWindow)
        if self._bound and self._bound_identity is None and catalog_has_bound:
            self._bound_identity = self._binding_identity()
        # Releases continue on the native owner after end-round. A late native
        # fault is safety-disarmed even while waiting, not hidden until next round.
        current = self._service.snapshot()
        if current.get("error"):
            self._armed = False
            self._timer.stop()
            self._stop()
            self._message = current["error"]
            self._publish()
            return
        if not self._next_round_ready():
            self._stop()
            self._message = "开箱连点保持开启；等待下一局开始后自动恢复"
            self._publish()
            return
        handle, reason = self._gate()
        if not handle:
            self._stop()
            self._message = reason
        elif not self._running:
            self._bound = str(self.coordinator.boundWindow)
            current = self._service.snapshot()
            if current.get("error"):
                self._armed = False
                self._timer.stop()
                self._stop()
                self._message = current["error"]
                self._publish()
                return
            if current.get("pendingReleaseVks"):
                self._message = "已准备；等待上次按键释放后恢复"
                self._publish()
                return
            if self._needs_release:
                try:
                    held = self._key_is_down(TRIGGER_VKS[self._service.snapshot()["holdKey"]])
                except Exception:
                    held = True  # Failed physical-state reads must never authorize input.
                if held:
                    self._message = "已准备；请松开触发键后再按住，才会连发"
                    self._publish()
                    return
                self._needs_release = False
            with self._lock:
                self._target = handle
            resume = getattr(self._service, "resume", None)
            result = (resume() if current.get("enabled") and callable(resume)
                      else self._service.update(dict(enabled=True)))
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
        if not enabled:
            try:
                merge_save(self._intent_path, INTENT_SCHEMA, dict(enabled=False))
                self._saved_enabled = False
            except OSError:
                self._message = "连点已停止，但关闭偏好保存失败；重启前请修复配置"
                self._publish()
                return dict(success=False, message=self._message)
        # Save/validate while suspended. update(enabled=True) owns native sync,
        # so it is called only by pollOnce after the complete foreground gate.
        result = self._service.update(dict(enabled=False, holdKey=hold_key,
                                          tapKey=tap_key, intervalMs=interval))
        self._message = result.get("message", "")
        if result.get("success") and enabled:
            state = self.input.snapshot
            if (self.coordinator.boundWindow and state.get("enabled")
                    and str(state.get("hwnd")) == str(self.coordinator.boundWindow)):
                try:
                    merge_save(self._intent_path, INTENT_SCHEMA, dict(enabled=True))
                except OSError:
                    self._message = "开启偏好保存失败，连点保持停止；请检查配置文件"
                    self._publish()
                    return dict(success=False, message=self._message)
                self._saved_enabled = True
                self._armed = True
                self._bound = str(self.coordinator.boundWindow)
                self._bound_identity = self._binding_identity()
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
        result = self._service.reconcile_reserved()
        if not result.get("success"):
            self._armed = False
            self._timer.stop()
            self._stop()
            self._message = result.get("message", "快捷键冲突，开箱连点已暂停")
        self.pollOnce()

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._armed = False
        self._timer.stop()
        self._stop()
        getattr(self._service, "shutdown", self._service.stop)()
        self._message = "开箱连点已停止"
        self._publish()
