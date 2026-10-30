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

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot

from client_paths import RESOURCE_ROOT as ROOT, WORK_ROOT
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))
from control_layout import preset_layout, validate_layout
from hotkey_bindings import parse_chord, validate_unique
from hotkey_keys import _primary_hotkey_to_vk
from opacity_controls import load_opacities

ACTION_ORDER = ("toggle-panel", "candidate-prev", "candidate-next", "confirm-round",
                "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round")
LEGACY_ACTIONS = ["F5", "F6", "F7", "F8", "F9", "F10", "F11", "", ""]
FRESH_ACTIONS = ["F9", "F6", "F7", "F8", "", "", "F11", "", "F10"]
BOOL_DEFAULTS = {"referenceSheetVisible": True, "sheetVisibilityEnabled": True,
                 "orbVisible": True, "reduceMotion": False, "uiSoundEnabled": False,
                 "difficultGridEnabled": False}
RANGES = {"sheetOpacity": (0., 1.), "referenceOpacity": (0., 1.),
          "interfaceOpacity": (.65, .95), "sheetScale": (.5, 1.5)}
DEFAULTS = {**BOOL_DEFAULTS, "sheetOpacity": .55, "referenceOpacity": .55,
            "interfaceOpacity": .82, "sheetScale": 1., "gameMode": "nightmare",
            "routeProfileId": "wang-qingxin", "modeRouteProfiles": {},
            "zhan10CoffinVariantId": "variant-01", "hotkey": "G",
            "actionHotkeys": FRESH_ACTIONS, "gameControlLayout": preset_layout(),
            "sheetPosition": None, "compactPosition": [24, 24]}


def _validate(key, value):
    if key not in DEFAULTS:
        raise ValueError("此设置尚未迁移，不能在这里修改")
    if key in BOOL_DEFAULTS:
        if type(value) is not bool:
            raise ValueError("需要开启或关闭值")
    elif key in RANGES:
        low, high = RANGES[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"需要 {low:g}–{high:g} 之间的数值")
        value = float(value)
    elif key == "gameMode":
        if value not in ("nightmare", "difficult"):
            raise ValueError("模式只能为 nightmare 或 difficult")
    elif key in ("routeProfileId", "zhan10CoffinVariantId"):
        if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError("请输入有效的配置 ID（1–128 个字符）")
    elif key == "modeRouteProfiles":
        if not isinstance(value, dict) or not set(value) <= {"nightmare", "difficult"}:
            raise ValueError("按模式路线必须是 nightmare / difficult 的配置表")
        for profile in value.values():
            _validate("routeProfileId", profile)
    elif key == "gameControlLayout":
        value = validate_layout(value)
    elif key in ("sheetPosition", "compactPosition"):
        if value is not None and (not isinstance(value, list) or len(value) != 2
                                  or any(type(v) is not int or not -(2**31) <= v < 2**31 for v in value)):
            raise ValueError("位置需要两个整数坐标 [x, y]，或空值")
    elif key == "hotkey":
        if not isinstance(value, str):
            raise ValueError("快捷键需要文本")
        value = parse_chord(value, _primary_hotkey_to_vk).name
    elif key == "actionHotkeys":
        if not isinstance(value, list) or len(value) != len(ACTION_ORDER) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"功能快捷键必须包含 {len(ACTION_ORDER)} 项，留空表示解绑")
        value = [parse_chord(v, _primary_hotkey_to_vk).name for v in value]
    return deepcopy(value)


def _validate_hotkeys(values):
    validate_unique({"地图键": parse_chord(values["hotkey"], _primary_hotkey_to_vk),
                     **{key: parse_chord(text, _primary_hotkey_to_vk)
                        for key, text in zip(ACTION_ORDER, values["actionHotkeys"])}})


def _legacy_actions(value, map_name):
    """Read legacy 6/7/8-slot arrays; preserve them on disk until edited."""
    if not isinstance(value, list) or len(value) not in (6, 7, 8, 9):
        return _validate("actionHotkeys", value)
    if not all(isinstance(item, str) for item in value):
        raise ValueError("功能快捷键需要文本")
    names = [parse_chord(item, _primary_hotkey_to_vk).name for item in value]
    if len(names) == 6:
        used = {parse_chord(item, _primary_hotkey_to_vk).vk for item in names}
        used.add(parse_chord(map_name, _primary_hotkey_to_vk).vk)
        # Matches the six-action migration in the legacy reader.
        names.append(next(name for name in ["F11", *(f"F{i}" for i in range(12, 25))]
                          if parse_chord(name, _primary_hotkey_to_vk).vk not in used))
    return names + [""] * (len(ACTION_ORDER) - len(names))


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

    def __init__(self, sandbox: Path):
        super().__init__()
        self.sandbox = Path(sandbox).resolve()
        self._check_sandbox()
        self.path = self.sandbox / "ui-settings.json"
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._job = None
        self._busy = False
        self._message = ""
        self._read_error = ""
        self._write_error = ""
        self._repairs = set()
        self._invalid = {}
        self._raw = {}
        self._disk_bytes = None
        self._baseline = deepcopy(DEFAULTS)
        self._draft = deepcopy(DEFAULTS)
        self.reload()

    values = Property("QVariantMap", lambda s: deepcopy(s._draft), notify=changed)
    persistedValues = Property("QVariantMap", lambda s: deepcopy(s._baseline), notify=changed)
    dirty = Property(bool, lambda s: s._draft != s._baseline or bool(s._repairs), notify=changed)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    message = Property(str, lambda s: s._message, notify=changed)

    def _check_sandbox(self):
        marker = self.sandbox / ".qt-lab-owner"
        if (not self.sandbox.is_relative_to(WORK_ROOT)
                or not marker.is_file() or marker.is_symlink()
                or marker.read_text(encoding="utf-8").strip() != "qt-quick-isolated-v1"):
            raise ValueError("Settings require a marked sandbox beneath this clone's work/")
        target = self.sandbox / "ui-settings.json"
        if target.is_symlink() or target.resolve().parent != self.sandbox:
            raise ValueError("Settings path must remain inside the marked sandbox")

    @Slot(str, "QVariant", result=bool)
    def setValue(self, key, value):
        if hasattr(value, "toVariant"):
            value = value.toVariant()
        try:
            value = _validate(key, value)
            proposal = {**self._draft, key: value}
            if key in ("hotkey", "actionHotkeys"):
                _validate_hotkeys(proposal)
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
        values = deepcopy(DEFAULTS)
        values["actionHotkeys"] = deepcopy(LEGACY_ACTIONS if data is not None else FRESH_ACTIONS)
        invalid = {}
        for key in DEFAULTS:
            if key in raw:
                try:
                    values[key] = (_legacy_actions(raw[key], values["hotkey"])
                                   if key == "actionHotkeys" else _validate(key, raw[key]))
                except (TypeError, ValueError) as error:
                    invalid[key] = str(error)
        try:
            _validate_hotkeys(values)
        except ValueError as error:
            invalid["hotkey/actionHotkeys"] = str(error)
        # Legacy reference opacity falls back to the stored sheet opacity.
        overlay, reference = load_opacities(raw)
        if "sheetOpacity" not in invalid:
            values["sheetOpacity"] = overlay
        if "referenceOpacity" not in invalid:
            values["referenceOpacity"] = reference
        self._raw, self._disk_bytes, self._invalid = raw, data, invalid
        self._repairs.clear()
        self._baseline = deepcopy(values)
        self._draft = values
        self._read_error = ""
        self._write_error = ""
        self._message = ("已读取；无效字段保留原值，只有编辑过的设置会写入：" + "、".join(invalid)
                         if invalid else "已读取隔离设置" if data is not None else "尚未保存设置；编辑后保存")
        self.changed.emit()
        return True

    @Slot(result=bool)
    def resetDraft(self):
        if self._busy:
            self._message = "正在保存；完成后可撤销草稿"
            self.changed.emit()
            return False
        self._draft = deepcopy(self._baseline)
        self._repairs.clear()
        self._message = self._read_error or self._write_error or "已撤销未保存的更改"
        self.changed.emit()
        return True

    def _write(self, payload, expected):
        self._check_sandbox()
        current = self.path.read_bytes() if self.path.exists() else None
        if current != expected:
            raise ValueError("文件已被其他操作修改；请重新读取后再保存")
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        # The legacy atomic writer lives in the Tk entrypoint. Keep its durable
        # fsync + replace contract here without importing that application.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.sandbox, prefix=".ui-settings-", suffix=".tmp",
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

    def _save_fields(self, keys):
        if self._busy:
            return False
        if self._read_error:
            self._message = self._read_error
            self.changed.emit()
            return False
        edits = {k: deepcopy(self._draft[k]) for k in keys
                 if self._draft[k] != self._baseline[k] or k in self._repairs}
        if not edits:
            self._message = "没有需要保存的更改"
            self.changed.emit()
            return False
        snapshot = {**deepcopy(self._baseline), **deepcopy(edits)}
        if set(edits) & {"hotkey", "actionHotkeys"}:
            try:
                _validate_hotkeys(snapshot)
            except ValueError as error:
                self._message = f"快捷键不能单独保存：{error}"
                self.changed.emit()
                return False
        payload = {**deepcopy(self._raw), **edits}
        expected = self._disk_bytes
        self._busy = True
        self._message = "正在保存隔离设置…"
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
        if error:
            self._write_error = f"保存失败：{error}；草稿已保留。"
            self._message = self._write_error
        else:
            self._raw, self._disk_bytes = payload, data
            self._baseline = snapshot
            self._invalid = {k: v for k, v in self._invalid.items() if k not in saved_keys}
            self._repairs.difference_update(saved_keys)
            self._write_error = ""
            self._message = "已保存；仍有新的未保存更改" if self.dirty else "已保存隔离设置"
        self.changed.emit()
        if not error:
            self.saved.emit()

    def shutdown(self):
        """Finish an already accepted write before application teardown."""
        self.pool.waitForDone()
