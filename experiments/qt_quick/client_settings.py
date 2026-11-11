"""Draft settings for the isolated Qt client. Never imports the Tk entrypoint.

Only explicitly edited legacy keys are written. Unknown fields and unsupported
runtime state survive a save verbatim as JSON values. Shortcut parsing stores
preferences only; this module never registers or polls native shortcuts.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QEvent, QCoreApplication, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QKeyEvent

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT, runtime_paths
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))
from season_context import CONTEXTS, MODES, KNOWN_MODES, context_key, normalize_season, selected_profile, season_options
from client_control_layout import load_layout, preset_layout, validate_layout
from floor_control_region import normalized_region
from client_manual_map import MANUAL_MAP_PICKER_DEFAULT_HOTKEY, MANUAL_PICKER_ACTION, SELECTION_MODES
from hotkey_bindings import parse_chord, validate_unique
from hotkey_keys import _primary_hotkey_to_vk, recorded_native_primary, NATIVE_ONLY_SHORTCUT_KEYS
from opacity_controls import load_opacities
from client_settings_values import (
    ACTION_ORDER, BOOL_DEFAULTS, DEFAULTS, FLOOR_CONTROL_MODES, FRESH_ACTIONS, LEGACY_ACTIONS,
    RANGES, _legacy_actions, _validate, _validate_floor_region, _validate_hotkeys, decode_settings,
)


class _Signals(QObject):
    done = Signal(object, object)


class _Write(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(), None)
        except Exception as error:
            self.signals.done.emit(None, str(error))


class SettingsService(QObject):
    changed = Signal()
    saved = Signal()
    controlsVisibilitySaveFailed = Signal(str)
    shortcutRecordingChanged = Signal(bool)

    def __init__(self, sandbox: Path):
        super().__init__()
        self.sandbox = Path(sandbox).resolve()
        self._check_sandbox()
        self.path = runtime_paths(self.sandbox).settings_path
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._job = None
        self._busy = False
        self._controls_pending = None
        self._message = ""
        self._read_error = ""
        self._write_error = ""
        self._repairs = set()
        self._invalid = {}
        self._raw = {}
        self._disk_bytes = None
        self._baseline = deepcopy(DEFAULTS)
        self._draft = deepcopy(DEFAULTS)
        # Fail closed until a valid settings read establishes an absent key or choice.
        self._baseline["automaticFailureEvidence"] = False
        self._draft["automaticFailureEvidence"] = False
        self._shortcut_native_event = None
        self._shortcut_target = ""
        self._shortcut_candidate = ""
        self._shortcut_ready = False
        self._shortcut_message = ""
        self._shortcut_primary_seen = False
        self._route_metadata = dict(context={}, options=[], message="连接当前全棺地图后可核对版本选项")
        self._route_job = None
        self._route_pending = None
        self._route_generation = 0
        self._route_runtime = None
        self._mode_change_guard = lambda: True
        self.reload()

    def _application_panel_shortcut(self):
        try:
            chord = parse_chord(self._baseline["actionHotkeys"][0], _primary_hotkey_to_vk)
            return "" if chord.vk in NATIVE_ONLY_SHORTCUT_KEYS else chord.name
        except (ValueError, IndexError):
            return ""

    applicationPanelShortcut = Property(str, _application_panel_shortcut, notify=changed)

    seasonOptions = Property("QVariantList", lambda s: season_options(), constant=True)
    values = Property("QVariantMap", lambda s: deepcopy(s._draft), notify=changed)
    persistedValues = Property("QVariantMap", lambda s: deepcopy(s._baseline), notify=changed)
    loadFailed = Property(bool, lambda s: bool(s._read_error), notify=changed)
    dirty = Property(bool, lambda s: s._draft != s._baseline or bool(s._repairs), notify=changed)
    controlLayoutDirty = Property(bool, lambda s: s._draft["gameControlLayout"] != s._baseline["gameControlLayout"]
                                  or "gameControlLayout" in s._repairs, notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    pendingWrites = Property(bool, lambda s: s._busy or s._controls_pending is not None, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)
    shortcutRecording = Property(bool, lambda s: bool(s._shortcut_target), notify=changed)
    shortcutTarget = Property(str, lambda s: s._shortcut_target, notify=changed)
    shortcutCandidate = Property(str, lambda s: s._shortcut_candidate, notify=changed)
    shortcutCandidateReady = Property(bool, lambda s: s._shortcut_ready, notify=changed)
    shortcutMessage = Property(str, lambda s: s._shortcut_message, notify=changed)
    coffinVariantOptions = Property("QVariantList", lambda s: deepcopy(s._route_metadata["options"]), notify=changed)
    coffinVariantContext = Property("QVariantMap", lambda s: deepcopy(s._route_metadata["context"]), notify=changed)
    coffinVariantMessage = Property(str, lambda s: s._route_metadata["message"], notify=changed)

    def refreshRoutePreferences(self, runtime):
        """Root calls after session/resource changes; one worker plus latest request."""
        self._route_runtime = runtime
        self._route_generation += 1
        if self._route_job is not None:
            self._route_pending = runtime
            return
        generation = self._route_generation
        self._route_job_generation = generation
        self._route_job = _Write(lambda: (generation, runtime.route_preferences_metadata()))
        self._route_job.signals.done.connect(self._route_preferences_finished, Qt.QueuedConnection)
        self.pool.start(self._route_job)

    @Slot(object, object)
    def _route_preferences_finished(self, result, error):
        self._route_job = None
        if self._route_job_generation != self._route_generation:
            metadata = None
        elif error:
            metadata = dict(context={}, options=[], message=f"全棺版本资源读取失败：{error}")
        elif result[1]["context"] != self._route_runtime.route_preferences_context():
            metadata = None
        else:
            metadata = result[1]
        if metadata is not None and metadata != self._route_metadata:
            self._route_metadata = metadata
            self.changed.emit()
        pending, self._route_pending = self._route_pending, None
        if pending is not None:
            self.refreshRoutePreferences(pending)

    @Slot(str, result=bool)
    def selectCoffinVariant(self, variant):
        context = self._route_metadata["context"]
        mode = self._draft["gameMode"]
        try:
            profile = selected_profile(self._draft)
        except ValueError:
            self._message = "赛季与模式组合尚未完成，请先选择有效组合"
            self.changed.emit()
            return False
        if (self._route_runtime is None or self._route_runtime.route_preferences_context() != context
                or context.get("mode") != mode or context.get("season", "s0") != self._draft["gameSeason"]
                or context.get("profileId") != profile
                or not any(row["id"] == variant and row["available"] for row in self._route_metadata["options"])):
            self._message = "当前地图/楼层没有该全棺版本的精确显示资源，请重新核对资源"
            self.changed.emit()
            return False
        return self.setValue("zhan10CoffinVariantId", variant)

    @Slot(str, int, result=bool)
    def moveControlModule(self, module, offset):
        layout = load_layout(self._draft["gameControlLayout"])
        if module not in layout["order"] or offset not in (-1, 1):
            return False
        index = layout["order"].index(module)
        target = index + offset
        if not 0 <= target < len(layout["order"]):
            return False
        layout["order"][index], layout["order"][target] = layout["order"][target], layout["order"][index]
        return self.setValue("gameControlLayout", layout)

    @Slot(str, result=bool)
    def setControlLayoutPreset(self, name):
        try:
            return self.setValue("gameControlLayout", preset_layout(name))
        except ValueError as error:
            self._message = str(error)
            self.changed.emit()
            return False

    @Slot(result=bool)
    def resetControlLayout(self):
        if self._busy:
            return False
        self._draft["gameControlLayout"] = deepcopy(self._baseline["gameControlLayout"])
        self._repairs.discard("gameControlLayout")
        self._message = self._read_error or self._write_error or "已撤销控件布局的未保存更改"
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def beginShortcutCapture(self, target):
        if target != "hotkey" and target not in ACTION_ORDER:
            return False
        if self._busy:
            return False
        was_recording = self.shortcutRecording
        self._shortcut_target = target
        self._shortcut_candidate = ""
        self._shortcut_ready = False
        self._shortcut_primary_seen = False
        self._shortcut_message = "请按一个主键，可同时按 Ctrl、Alt、Shift；Esc 取消"
        self._shortcut_native_event = None
        app = QGuiApplication.instance()
        if app is not None and not was_recording:
            app.installEventFilter(self)
        if not was_recording:
            self.shortcutRecordingChanged.emit(True)
        self.changed.emit()
        return True

    def _candidate_from_primary(self, primary, modifiers):
        if not primary:
            raise ValueError("未识别到这个键，请使用主键或点击解绑")
        if modifiers & Qt.MetaModifier.value:
            raise ValueError("暂不支持 Windows 修饰键")
        labels = [label for label, flag in (("Ctrl", Qt.ControlModifier), ("Alt", Qt.AltModifier),
                                           ("Shift", Qt.ShiftModifier)) if modifiers & flag.value]
        return parse_chord("+".join([*labels, primary]), _primary_hotkey_to_vk).name

    def _offer_shortcut(self, primary, modifiers):
        try:
            self._shortcut_candidate = self._candidate_from_primary(primary, modifiers)
            self._shortcut_ready = True
            self._shortcut_message = "点击保存并生效即可保存此快捷键"
            self.changed.emit()
            return True
        except ValueError as error:
            self._shortcut_candidate = ""
            self._shortcut_ready = False
            self._shortcut_message = str(error)
            self.changed.emit()
            return False

    def eventFilter(self, watched, event):
        # Only the focused, owned recorder's Qt events. No global hook, polling
        # or saved text. QML KeyEvent omits nativeVirtualKey, so bridge it once
        # to the synchronous capture slot without interpreting the typed glyph.
        if self.shortcutRecording and isinstance(event, QKeyEvent) and event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            app = QGuiApplication.instance()
            focus = app.focusObject() if app is not None else None
            if focus is not None and focus.objectName() in ("shortcutCaptureArea", "onboardingRecorder"):
                self._shortcut_native_event = (event.key(), event.modifiers().value, event.text(),
                    event.type() == QEvent.KeyRelease, event.nativeVirtualKey())
        return False

    @Slot(int, int, str, bool, result=bool)
    def captureShortcutKey(self, key, modifiers, text, released):
        """Consume an owned Qt key event only; no global polling or registration."""
        if not self.shortcutRecording:
            return False
        native_event, self._shortcut_native_event = self._shortcut_native_event, None
        native_vk = native_event[4] if native_event and native_event[:4] == (key, modifiers, text, released) else 0
        if key == Qt.Key_Escape:
            self.cancelShortcutCapture()
            return False
        modifier_names = {Qt.Key_Control: "CTRL", Qt.Key_Alt: "ALT", Qt.Key_Shift: "SHIFT"}
        if key in modifier_names:
            if released and not self._shortcut_primary_seen:
                remaining = modifiers & (Qt.ControlModifier.value | Qt.AltModifier.value | Qt.ShiftModifier.value)
                # Qt may include the released modifier itself in KeyEvent.
                # Only OTHER held modifiers make this an incomplete chord.
                own = {Qt.Key_Control: Qt.ControlModifier.value,
                       Qt.Key_Alt: Qt.AltModifier.value,
                       Qt.Key_Shift: Qt.ShiftModifier.value}[key]
                remaining &= ~own
                if remaining:
                    self._shortcut_primary_seen = True
                    self._shortcut_ready = False
                    self._shortcut_message = "修饰键组合需要一个主键，请重新录入"
                    self.changed.emit()
                    return False
                return self._offer_shortcut(modifier_names[key], 0)
            return False
        if released:
            return False
        self._shortcut_primary_seen = True
        named = {Qt.Key_Backspace: "BACKSPACE", Qt.Key_Tab: "TAB", Qt.Key_Backtab: "TAB",
                 Qt.Key_Return: "ENTER", Qt.Key_Enter: "ENTER", Qt.Key_Pause: "PAUSE",
                 Qt.Key_CapsLock: "CAPSLOCK", Qt.Key_Space: "SPACE", Qt.Key_PageUp: "PAGEUP",
                 Qt.Key_PageDown: "PAGEDOWN", Qt.Key_End: "END", Qt.Key_Home: "HOME",
                 Qt.Key_Left: "LEFT", Qt.Key_Up: "UP", Qt.Key_Right: "RIGHT", Qt.Key_Down: "DOWN",
                 Qt.Key_Print: "PRINTSCREEN", Qt.Key_Insert: "INSERT", Qt.Key_Delete: "DELETE",
                 Qt.Key_NumLock: "NUMLOCK", Qt.Key_ScrollLock: "SCROLLLOCK",
                 Qt.Key_VolumeMute: "VOLUME_MUTE", Qt.Key_VolumeDown: "VOLUME_DOWN",
                 Qt.Key_VolumeUp: "VOLUME_UP", Qt.Key_MediaNext: "MEDIA_NEXT",
                 Qt.Key_MediaPrevious: "MEDIA_PREV", Qt.Key_MediaStop: "MEDIA_STOP",
                 Qt.Key_MediaPlay: "MEDIA_PLAY_PAUSE", Qt.Key_MediaTogglePlayPause: "MEDIA_PLAY_PAUSE"}
        native_primary = recorded_native_primary(native_vk)
        if native_primary:
            primary = native_primary
        elif Qt.Key_F1 <= key <= Qt.Key_F24:
            primary = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
            primary = chr(key)
        else:
            primary = named.get(key, text if modifiers & Qt.KeypadModifier.value else "")
        if modifiers & Qt.KeypadModifier.value and not native_primary:
            primary = ("NUMPAD" + primary if primary.isdigit() and len(primary) == 1
                       else {"*": "NUM*", "+": "NUM+", "-": "NUM-", ".": "NUM.", "/": "NUM/"}.get(primary, primary))
        return self._offer_shortcut(primary, modifiers)

    @Slot(int, int, result=bool)
    def captureShortcutMouse(self, button, modifiers):
        if not self.shortcutRecording:
            return False
        primary = {Qt.RightButton.value: "MOUSE2", Qt.MiddleButton.value: "MOUSE3",
                   Qt.BackButton.value: "MOUSE4", Qt.ForwardButton.value: "MOUSE5"}.get(button)
        if primary is None:
            return False
        self._shortcut_primary_seen = True
        return self._offer_shortcut(primary, modifiers)

    @Slot(result=bool)
    def clearShortcutCandidate(self):
        if not self.shortcutRecording:
            return False
        self._shortcut_candidate, self._shortcut_ready = "", True
        self._shortcut_message = "点击保存并生效即可解绑此快捷键"
        self.changed.emit()
        return True

    @Slot(result=bool)
    def confirmShortcutCapture(self):
        if self._busy or not self.shortcutRecording or not self._shortcut_ready:
            return False
        target = self._shortcut_target
        candidate = self._shortcut_candidate
        key = "hotkey" if target == "hotkey" else "actionHotkeys"
        value = deepcopy(self._baseline[key])
        index = None if target == "hotkey" else ACTION_ORDER.index(target)
        if index is None:
            value = candidate
        else:
            value[index] = candidate
        original_draft = deepcopy(self._draft[key] if index is None else self._draft[key][index])
        accepted = self._save_fields([key], {key: value})
        if accepted:
            self._capture_commit = (key, index, candidate, original_draft)
        else:
            self._shortcut_message = self._message
            self.changed.emit()
        return accepted

    @Slot()
    def cancelShortcutCapture(self):
        if not self.shortcutRecording:
            return
        self._shortcut_native_event = None
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._shortcut_target = ""
        self._shortcut_candidate = ""
        self._shortcut_ready = False
        self._shortcut_message = ""
        self.shortcutRecordingChanged.emit(False)
        self.changed.emit()

    def _check_sandbox(self):
        paths = runtime_paths(self.sandbox)
        if hasattr(self, "path") and self.path != paths.settings_path:
            raise ValueError("Settings path no longer matches the installation")

    def setModeChangeGuard(self, guard):
        if not callable(guard):
            raise TypeError("mode change guard must be callable")
        self._mode_change_guard = guard

    def _mode_change_allowed(self, value, key="gameMode"):
        if value == self._baseline.get(key) or self._mode_change_guard():
            return True
        self._message = "请先结束本局，再切换游戏模式"
        self.changed.emit()
        return False

    @Slot(str, "QVariant", result=bool)
    def setValue(self, key, value):
        if hasattr(value, "toVariant"):
            value = value.toVariant()
        try:
            value = _validate(key, value)
            if key in ("gameMode", "gameSeason") and not self._mode_change_allowed(value, key):
                return False
            proposal = {**self._draft, key: value}
            from season_context import is_retired_mode, RETIRED_MODE_MESSAGE
            if (key in ("gameMode", "gameSeason") and self._draft.get(key) != value
                    and is_retired_mode(proposal["gameMode"], proposal["gameSeason"])):
                self._message = RETIRED_MODE_MESSAGE
                self.changed.emit()
                return False
            if key in ("hotkey", "actionHotkeys"):
                _validate_hotkeys(proposal, self._raw.get("actionHotkeys"))
            if self._draft.get(key) == value and key not in self._invalid:
                return True
            self._draft[key] = value
            if key in self._invalid:
                self._repairs.add(key)
            self._message = self._read_error or self._write_error or "有未保存的更改"
            self.changed.emit()
            return True
        except (ValueError, TypeError) as error:
            self._message = self._read_error or f"{key}：{error}"
            self.changed.emit()
            return False

    @Slot(result=bool)
    def reload(self):
        if self._busy:
            self._message = "正在保存；完成后可重新读取"
            self.changed.emit()
            return False
        self.cancelShortcutCapture()
        try:
            self._check_sandbox()
            data = self.path.read_bytes() if self.path.exists() else None
            raw = json.loads(data.decode("utf-8")) if data is not None else {}
            if not isinstance(raw, dict):
                raise ValueError("设置文件必须是 JSON 对象")
            # Reject nonstandard JSON numbers instead of re-emitting invalid JSON.
            json.dumps(raw, allow_nan=False)
        except (OSError, ValueError, UnicodeError) as error:
            self._read_error = f"设置读取失败：{error}。原文件已保留；修复文件后重新读取。"
            self._message = self._read_error
            self.changed.emit()
            return False
        values, invalid = decode_settings(raw, existing_file=data is not None)
        self._raw, self._disk_bytes, self._invalid = raw, data, invalid
        self._repairs.clear()
        self._baseline = deepcopy(values)
        self._draft = values
        self._read_error = ""
        self._write_error = ""
        self._message = ("已读取；无效字段保留原值，只有编辑过的设置会写入：" + "、".join(invalid)
                         if invalid else "已读取设置" if data is not None else "尚未保存设置；编辑后保存")
        from season_context import is_retired_mode, RETIRED_MODE_MESSAGE
        if is_retired_mode(values["gameMode"], values["gameSeason"]):
            self._message = RETIRED_MODE_MESSAGE
        self.changed.emit()
        return True

    @Slot(result=bool)
    def resetDraft(self):
        if self._busy:
            self._message = "正在保存；完成后可撤销草稿"
            self.changed.emit()
            return False
        self.cancelShortcutCapture()
        self._draft = deepcopy(self._baseline)
        self._repairs.clear()
        self._message = self._read_error or self._write_error or "已撤销未保存的更改"
        self.changed.emit()
        return True

    def _write(self, payload, expected):
        self._check_sandbox()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current = self.path.read_bytes() if self.path.exists() else None
        if current != expected:
            raise ValueError("文件已被其他操作修改；请重新读取后再保存")
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        # The legacy atomic writer lives in the Tk entrypoint. Keep its durable
        # fsync + replace contract here without importing that application.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.path.parent, prefix=".ui-settings-", suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_sandbox()
            current = self.path.read_bytes() if self.path.exists() else None
            if current != expected:
                raise ValueError("文件已被其他操作修改；请重新读取后再保存")
            os.replace(temporary, self.path)
            return text.encode("utf-8")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @Slot(bool, result=bool)
    def persistControlsVisibility(self, visible):
        """Explicit Qt intent only; never infer it from Tk's auto-saved orbVisible.

        One latest-only queue joins the existing writer, without accepting other
        open drafts. Even explicit True on an old/default file must write a key.
        """
        if type(visible) is not bool:
            return False
        self._draft["qtControlsVisible"] = visible
        self._controls_pending = visible
        self.changed.emit()
        if self._busy:
            return True
        return self._save_pending_controls()

    def _save_pending_controls(self):
        visible, self._controls_pending = self._controls_pending, None
        if visible is None:
            return True
        # A newer choice may return to the original baseline while an older
        # opposite write was active. Compare after that write has committed.
        if (self._raw.get("qtControlsVisible") is visible
                and self._baseline["qtControlsVisible"] is visible):
            self.changed.emit()
            return True
        accepted = self._save_fields(["qtControlsVisible"], {"qtControlsVisible": visible})
        if not accepted:
            self.controlsVisibilitySaveFailed.emit(self._message)
        return accepted

    @Slot(result=bool)
    def save(self):
        return self._save_fields(list(DEFAULTS))

    @Slot("QVariantList", result=bool)
    def saveFields(self, keys):
        """Commit selected fields without accepting the rest of an open draft."""
        if hasattr(keys, "toVariant"):
            keys = keys.toVariant()
        if (not isinstance(keys, list) or not keys
                or any(not isinstance(key, str) or key not in DEFAULTS for key in keys)):
            self._message = self._read_error or self._write_error or "请选择已支持的设置字段"
            self.changed.emit()
            return False
        return self._save_fields(keys)

    def _save_fields(self, keys, explicit_edits=None):
        if self._busy:
            return False
        if self._read_error:
            self._message = self._read_error
            self.changed.emit()
            return False
        edits = {k: deepcopy(self._draft[k]) for k in keys
                 if self._draft[k] != self._baseline[k] or k in self._repairs}
        if explicit_edits is not None:
            edits = deepcopy(explicit_edits)
        if not edits:
            self._message = "没有需要保存的更改"
            self.changed.emit()
            return False
        if any(key in edits and not self._mode_change_allowed(edits[key], key)
               for key in ("gameMode", "gameSeason")):
            return False
        snapshot = {**deepcopy(self._baseline), **deepcopy(edits)}
        try:
            context_key(snapshot["gameMode"], snapshot["gameSeason"])
        except ValueError:
            self._message = "赛季与模式组合无效；请一起保存有效的赛季和模式"
            self.changed.emit()
            return False
        if set(edits) & {"hotkey", "actionHotkeys"}:
            try:
                _validate_hotkeys(snapshot, self._raw.get("actionHotkeys"))
            except ValueError as error:
                self._message = f"快捷键不能单独保存：{error}"
                self.changed.emit()
                return False
        payload = {**deepcopy(self._raw), **edits}
        try:
            context_key(payload.get("gameMode", DEFAULTS["gameMode"]), payload.get("gameSeason", DEFAULTS["gameSeason"]))
        except ValueError:
            self._message = "原设置中的赛季与模式组合无效，请先修正模式；原文件已保留"
            self.changed.emit()
            return False
        if self._disk_bytes is None:
            # A new file otherwise looks like a legacy install on restart and
            # replaces untouched fresh shortcuts with LEGACY_ACTIONS.
            payload.setdefault("actionHotkeys", deepcopy(snapshot["actionHotkeys"]))
        if "actionHotkeys" in edits:
            stored = self._raw.get("actionHotkeys")
            if isinstance(stored, list) and len(stored) in (9, 10):
                # Keep untouched source spellings and unknown slots verbatim.
                merged = deepcopy(stored)
                for index, name in enumerate(edits["actionHotkeys"]):
                    if "actionHotkeys" in self._invalid or name != self._baseline["actionHotkeys"][index]:
                        if index < len(merged):
                            merged[index] = name
                        elif name:
                            # A nine-slot file only grows when slot 10 is bound.
                            merged.append(name)
                payload["actionHotkeys"] = merged
        expected = self._disk_bytes
        self._busy = True
        self._message = "正在保存设置…"
        job = self._job = _Write(lambda: self._write(payload, expected))
        # A QObject receiver ensures completion and property signals are on the
        # UI thread; snapshot is held until the one active write completes.
        self._saving = (snapshot, payload, set(edits))
        job.signals.done.connect(self._finished, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)
        return True

    @Slot(object, object)
    def _finished(self, data, error):
        snapshot, payload, saved_keys = self._saving
        self._busy = False
        self._job = None
        capture_commit = getattr(self, "_capture_commit", None)
        self._capture_commit = None
        if error:
            self._write_error = f"保存失败：{error}；草稿已保留。"
            self._message = self._write_error
            if capture_commit and self.shortcutRecording:
                self._shortcut_message = self._write_error
        else:
            self._raw, self._disk_bytes = payload, data
            self._baseline = snapshot
            if capture_commit:
                key, index, candidate, original_draft = capture_commit
                if index is None:
                    if self._draft[key] == original_draft:
                        self._draft[key] = candidate
                elif self._draft[key][index] == original_draft:
                    self._draft[key][index] = candidate
                self.cancelShortcutCapture()
            self._invalid = {k: v for k, v in self._invalid.items() if k not in saved_keys}
            self._repairs.difference_update(saved_keys)
            self._write_error = ""
            self._message = "已保存；仍有新的未保存更改" if self.dirty else "已保存设置"
        self.changed.emit()
        if not error:
            self.saved.emit()
        elif "qtControlsVisible" in saved_keys:
            self.controlsVisibilitySaveFailed.emit(self._message)
        # At most one latest request; a failed write is not retried by itself.
        self._save_pending_controls()

    def shutdown(self):
        """Finish an already accepted write before application teardown."""
        self.cancelShortcutCapture()
        controls_write = (self._controls_pending is not None
                          or self._busy and "qtControlsVisible" in self._saving[2])
        self.pool.waitForDone()
        if controls_write:
            # Only controls need this extra latest-only drain. Keep ordinary
            # settings teardown unchanged; no general event-pumping retry loop.
            QCoreApplication.sendPostedEvents(self, QEvent.MetaCall)
            self.pool.waitForDone()
            QCoreApplication.sendPostedEvents(self, QEvent.MetaCall)
