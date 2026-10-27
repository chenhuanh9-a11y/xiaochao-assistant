"""开箱连点: while the trigger key is physically held, repeat one key in the game.

Runs on a daemon thread inside the assistant. The trigger is read with
GetAsyncKeyState and must differ from the repeated key, so injected presses
can never be mistaken for the player still holding the trigger. Presses are
only sent while the game window is in the foreground, and every injected
key-down is paired with a key-up, including on stop and shutdown.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time

SCHEMA = "xingtu-chest-auto-tap-v1"
DEFAULT_TAP_KEY = "F"
TRIGGER_PREFERENCES = ("F8", "F12", "F4", "F3", "F2")
DEFAULT_INTERVAL_MS = 90
MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 500
PRESS_MS = 90  # The game can drop a zero-length press; hold each one briefly.
POLL_SECONDS = .005

KEY_NAMES = {
    **{f"F{index}": 0x6F + index for index in range(1, 13)},
    **{letter: ord(letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{digit: ord(digit) for digit in "0123456789"},
    "Space": 0x20, "Tab": 0x09, "Enter": 0x0D,
}
MOUSE_TRIGGERS = {"鼠标侧键 1": 0x05, "鼠标侧键 2": 0x06}
TAP_KEYS = tuple(name for name in KEY_NAMES if not name.startswith("F") or len(name) == 1)
TRIGGER_KEYS = (*(f"F{index}" for index in range(1, 13)), *MOUSE_TRIGGERS,
                *(name for name in KEY_NAMES if not name.startswith("F") or len(name) == 1))
TRIGGER_VKS = {**KEY_NAMES, **MOUSE_TRIGGERS}

if sys.platform == "win32":
    from ctypes import wintypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t))

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t))

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = (("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))

    class _INPUTUNION(ctypes.Union):
        # The union must include MOUSEINPUT, its largest member, or SendInput
        # rejects the size (40 bytes on x64) with ERROR_INVALID_PARAMETER.
        _fields_ = (("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT))

    class _INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetAncestor.restype = wintypes.HWND
else:
    _user32 = None

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


def _send_key(vk: int, up: bool) -> int:
    """Send one scan-code key event; returns 0 or the Windows error code."""
    scan = _user32.MapVirtualKeyW(vk, 0)
    if not scan:
        return -1
    item = _INPUT(type=1)
    item.u.ki = _KEYBDINPUT(0, scan, 0x0008 | (0x0001 if vk in EXTENDED_VKS else 0) | (0x0002 if up else 0), 0, 0)
    sent = _user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))
    return 0 if sent == 1 else (ctypes.get_last_error() or -1)


def key_down(vk: int) -> bool:
    return bool(_user32 is not None and _user32.GetAsyncKeyState(vk) & 0x8000)


def same_top_window(first, second) -> bool:
    if _user32 is None or not first or not second:
        return False
    return _user32.GetAncestor(first, 2) == _user32.GetAncestor(second, 2)


def foreground_window():
    return _user32.GetForegroundWindow() if _user32 is not None else None


def validate(settings: dict, reserved: dict[int, str]) -> dict:
    """Normalised settings or ValueError with a message for the player."""
    tap, hold = str(settings.get("tapKey") or ""), str(settings.get("holdKey") or "")
    if tap not in KEY_NAMES:
        raise ValueError("连发按键请选一个键盘按键，例如 F、E 或空格。")
    if hold not in TRIGGER_VKS:
        raise ValueError("触发按键无效，请从列表里选择。")
    if tap == hold:
        raise ValueError("触发按键不能和连发按键相同。")
    for name, vk in (("触发按键", TRIGGER_VKS[hold]), ("连发按键", KEY_NAMES[tap])):
        if vk in reserved:
            raise ValueError(f"{name}已被“{reserved[vk]}”使用，请换一个键。")
    try:
        interval = round(float(settings.get("intervalMs")))
    except (TypeError, ValueError):
        raise ValueError(f"连点间隔请填 {MIN_INTERVAL_MS}–{MAX_INTERVAL_MS} 毫秒。") from None
    if not MIN_INTERVAL_MS <= interval <= MAX_INTERVAL_MS:
        raise ValueError(f"连点间隔请填 {MIN_INTERVAL_MS}–{MAX_INTERVAL_MS} 毫秒。")
    return dict(enabled=settings.get("enabled") is True, tapKey=tap, holdKey=hold, intervalMs=interval)


class ChestAutoTap:
    """Owns the settings file and the background tapping thread."""

    def __init__(self, path: Path, *, target_window, reserved=lambda: {}):
        self.path = Path(path)
        self._target_window = target_window  # Callable returning the game HWND or None.
        self._reserved = reserved
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.status = "未开启"
        self.holding = False
        self.error = ""
        self.available = _user32 is not None
        self.settings = self._load()

    def _defaults(self) -> dict:
        taken = self._reserved()
        hold = next((name for name in TRIGGER_PREFERENCES if KEY_NAMES[name] not in taken), "F12")
        return dict(enabled=False, tapKey=DEFAULT_TAP_KEY, holdKey=hold, intervalMs=DEFAULT_INTERVAL_MS)

    def _load(self) -> dict:
        defaults = self._defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
                return validate({**defaults, **raw}, {})
        except (OSError, ValueError):
            pass
        return defaults

    def update(self, settings: dict) -> dict:
        """Validate, save and apply. Returns {success, message}."""
        try:
            normalised = validate({**self.settings, **settings}, self._reserved())
        except ValueError as error:
            return dict(success=False, message=str(error))
        if normalised["enabled"] and not self.available:
            return dict(success=False, message="开箱连点只能在 Windows 上使用。")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(schema=SCHEMA, **normalised), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            return dict(success=False, message="保存失败，请检查软件目录是否可写。")
        with self._lock:
            self.settings = normalised
        self.sync()
        if not normalised["enabled"]:
            return dict(success=True, message="开箱连点已关闭。")
        return dict(success=True, message=f"已开启：在游戏里按住 {normalised['holdKey']}，"
                                          f"每 {normalised['intervalMs']} 毫秒连发 {normalised['tapKey']}。")

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.settings, available=self.available, status=self.status,
                        holding=self.holding, error=self.error)

    def sync(self) -> None:
        """Start or stop the worker to match the saved setting."""
        enabled = self.settings["enabled"] and self.available
        running = self._thread is not None and self._thread.is_alive()
        if enabled and not running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="chest-auto-tap", daemon=True)
            self._thread.start()
        elif not enabled and running:
            self.stop()
        if not enabled:
            self._set_status("未开启" if self.available else "仅支持 Windows", holding=False)

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    def _set_status(self, text, *, holding=None, error=None):
        with self._lock:
            self.status = text
            if holding is not None:
                self.holding = holding
            if error is not None:
                self.error = error

    def _run(self):
        next_tap = 0.0
        self._set_status("等待按住触发键", holding=False, error="")
        while not self._stop.is_set():
            with self._lock:
                settings = dict(self.settings)
            hold_vk, tap_vk = TRIGGER_VKS[settings["holdKey"]], KEY_NAMES[settings["tapKey"]]
            if not key_down(hold_vk):
                if self.holding:
                    self._set_status(f"已松开 {settings['holdKey']}，连点已停止", holding=False)
                next_tap = 0.0
                time.sleep(POLL_SECONDS)
                continue
            if not same_top_window(foreground_window(), self._target_window()):
                self._set_status("游戏窗口不在前台，暂不连发", holding=False)
                time.sleep(POLL_SECONDS * 4)
                continue
            now = time.perf_counter()
            if now < next_tap:
                time.sleep(min(POLL_SECONDS, next_tap - now))
                continue
            error = _send_key(tap_vk, up=False)
            if error:
                self._set_status(f"发送按键失败（Windows {error}）；游戏以管理员身份运行时，请用管理员身份启动星图",
                                 holding=False, error=str(error))
                time.sleep(.5)
                continue
            try:
                # Stop promptly if the trigger is released mid-press, but always release the key.
                deadline = now + PRESS_MS / 1000
                while time.perf_counter() < deadline and not self._stop.is_set() and key_down(hold_vk):
                    time.sleep(POLL_SECONDS)
            finally:
                _send_key(tap_vk, up=True)
            next_tap = now + max(settings["intervalMs"], PRESS_MS) / 1000
            self._set_status(f"正在连发 {settings['tapKey']}，松开 {settings['holdKey']} 停止", holding=True, error="")
        self._set_status("未开启", holding=False)
