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
PRESS_MS = 90  # Maximum hold; shorter periods use a 50% duty cycle.
POLL_SECONDS = .005
RELEASE_RETRIES = 3  # In addition to the first key-up attempt.
RELEASE_RETRY_SECONDS = .05

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
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"连点间隔请填 {MIN_INTERVAL_MS}–{MAX_INTERVAL_MS} 毫秒。") from None
    if not MIN_INTERVAL_MS <= interval <= MAX_INTERVAL_MS:
        raise ValueError(f"连点间隔请填 {MIN_INTERVAL_MS}–{MAX_INTERVAL_MS} 毫秒。")
    return dict(enabled=settings.get("enabled") is True, tapKey=tap, holdKey=hold, intervalMs=interval)


class ChestAutoTap:
    """One reusable daemon owns all input and the outstanding-release ledger.

    stop() only suspends this worker; it never waits on Windows input or joins.
    A failed release blocks all new presses until it has been confirmed sent.
    After the bounded retry budget, the ledger stays visible for diagnosis.
    """

    def __init__(self, path: Path, *, target_window, reserved=lambda: {}):
        self.path = Path(path)
        self._target_window = target_window  # Callable returning the game HWND or None.
        self._reserved = reserved
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._generation = 0
        self._pending_releases = {}  # VK -> number of key-up attempts.
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
                settings = validate({**defaults, **raw}, {})
                try:
                    return validate(settings, self._reserved())
                except ValueError as error:
                    self.error = self.status = f"开箱连点已停用：{error}"
                    return dict(settings, enabled=False)
        except (OSError, ValueError):
            pass
        return defaults

    def _save(self, settings: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(schema=SCHEMA, **settings), ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, self.path)

    def _disable(self) -> dict:
        with self._lock:
            self.settings = dict(self.settings, enabled=False)
            self._generation += 1
            self._stop.set()
            self.holding = False
            settings = dict(self.settings)
        self._wake.set()
        return settings

    def reconcile_reserved(self) -> dict:
        """Call after rebinding assistant shortcuts (including combination VKs).

        Returns {success, message}. A conflict disables immediately, wakes the
        existing worker for release, and best-effort persists disabled state.
        snapshot() carries the sticky reason, even if saving fails.
        """
        with self._lock:
            current = dict(self.settings)
        try:
            validate(current, self._reserved())
        except ValueError as error:
            disabled = self._disable()
            message = f"开箱连点已停用：{error}"
            try:
                self._save(disabled)
            except OSError:
                message += " 停用状态保存失败，请检查软件目录是否可写。"
            self._set_status(message, holding=False, error=message)
            return dict(success=False, message=message)
        return dict(success=True, message="开箱连点按键检查通过。")

    def update(self, settings: dict) -> dict:
        """Apply a change; enabled=False always suspends before validation/I/O.

        success=False on a disable request means some settings were not saved;
        runtime enabled is still False. Read snapshot() to refresh the UI.
        """
        disabling = settings.get("enabled") is False
        if disabling:
            current = self._disable()
        else:
            self.reconcile_reserved()
            with self._lock:
                current = dict(self.settings)
        with self._lock:
            generation = self._generation
        validation_error = ""
        try:
            normalised = validate({**current, **settings}, self._reserved())
        except ValueError as error:
            if not disabling:
                return dict(success=False, message=str(error))
            normalised = current
            validation_error = f" 其他设置未应用：{error}"
        if normalised["enabled"] and not self.available:
            return dict(success=False, message="开箱连点只能在 Windows 上使用。")
        with self._lock:
            if (normalised["enabled"] and self._pending_releases
                    and (self._stop.is_set() or any(self._pending_releases.values()))):
                return dict(success=False, message="仍有按键未确认释放，开箱连点保持停用；请检查状态。")
        try:
            self._save(normalised)
        except OSError:
            if disabling:
                return dict(success=False, message="开箱连点已关闭，但保存失败；重启前请检查软件目录是否可写。"
                            + validation_error)
            return dict(success=False, message="保存失败，请检查软件目录是否可写。")
        with self._lock:
            interrupted = normalised["enabled"] and self._generation != generation
            if interrupted:
                current = dict(self.settings)
            else:
                self.settings = normalised
                self._generation += 1
        if interrupted:
            # A worker failure/stop during disk I/O takes priority over enabling.
            try:
                self._save(current)
            except OSError:
                return dict(success=False, message="连点状态已变化，未开启；当前状态保存失败，请检查软件目录。")
            return dict(success=False, message="连点状态已变化，未开启；请检查状态后重新设置。")
        self.sync()
        if not normalised["enabled"]:
            return dict(success=not validation_error, message="开箱连点已关闭。" + validation_error)
        return dict(success=True, message=f"已开启：在游戏里按住 {normalised['holdKey']}，"
                    f"每 {normalised['intervalMs']} 毫秒开始一次 {normalised['tapKey']} 按键，"
                    f"每次按住 {min(PRESS_MS, normalised['intervalMs'] / 2):g} 毫秒。")

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.settings, available=self.available, status=self.status,
                        holding=self.holding, error=self.error,
                        pendingReleaseVks=sorted(self._pending_releases),
                        releaseRetriesExhausted=any(attempts > RELEASE_RETRIES
                                                   for attempts in self._pending_releases.values()))

    def sync(self) -> None:
        """Wake the single worker; never join it or replace a live thread."""
        with self._lock:
            enabled = (self.settings["enabled"] and self.available
                       and not any(self._pending_releases.values()))
            if enabled:
                self._stop.clear()
            else:
                self._stop.set()
            running = self._thread is not None and self._thread.is_alive()
            if (enabled or self._pending_releases) and not running:
                self._thread = threading.Thread(target=self._run, name="chest-auto-tap", daemon=True)
                self._thread.start()
        self._wake.set()
        if not enabled:
            self._set_status("未开启" if self.available else "仅支持 Windows", holding=False)

    def stop(self, timeout: float = 1.0) -> None:
        """Suspend immediately; timeout is retained for caller compatibility.

        The daemon remains available for releases/re-enabling. It cannot keep
        the process alive, and the UI never waits for a stuck Windows call.
        """
        with self._lock:
            self._generation += 1
            self._stop.set()
        self._wake.set()
        self._set_status("未开启", holding=False)

    def _set_status(self, text, *, holding=None, error=None):
        with self._lock:
            if holding is not None:
                self.holding = holding
            if error is not None:
                if error and error not in self.error:
                    self.error = f"{self.error} {error}".strip()
            self.status = self.error or text

    def _fault(self, message):
        self._disable()
        self._set_status(message, holding=False, error=message)

    def _active(self, generation):
        with self._lock:
            return (not self._stop.is_set() and self.settings["enabled"]
                    and self._generation == generation)

    def _in_game(self):
        return same_top_window(foreground_window(), self._target_window())

    def _release_pending(self):
        """One attempt per eligible key; never forget a failed/uncertain key-up."""
        with self._lock:
            pending = list(self._pending_releases.items())
        for vk, attempts in pending:
            if attempts > RELEASE_RETRIES:
                continue
            try:
                error = _send_key(vk, up=True)
            except Exception as exception:
                error = f"{type(exception).__name__}: {exception}"
            if not error:
                with self._lock:
                    self._pending_releases.pop(vk, None)
            else:
                with self._lock:
                    self._pending_releases[vk] = attempts + 1
                self._fault(f"释放按键 VK {vk} 失败（Windows {error}）；已停止连点并有限重试。"
                            "Windows 未确认释放，切换前台或退出助手也不能保证游戏已收到抬键。")

    def _run(self):
        next_tap = 0.0
        while True:
            self._wake.clear()
            with self._lock:
                settings = dict(self.settings)
                generation = self._generation
                pending = bool(self._pending_releases)
            if pending:
                with self._lock:
                    retrying = any(0 < count <= RELEASE_RETRIES
                                   for count in self._pending_releases.values())
                if retrying:
                    # Only the worker waits. UI wakes cannot consume the retry
                    # budget early, and no event clear can lose a re-enable.
                    time.sleep(RELEASE_RETRY_SECONDS)
                self._release_pending()
                with self._lock:
                    retryable = any(count <= RELEASE_RETRIES for count in self._pending_releases.values())
                    exhausted = bool(self._pending_releases) and not retryable
                if exhausted:
                    self._set_status("", error="释放重试已耗尽；待释放账本已保留，请手动检查游戏按键状态。")
                    self._wake.wait()
                continue
            if not self._active(generation):
                self._set_status("未开启", holding=False)
                next_tap = 0.0
                self._wake.wait()
                continue
            try:
                hold_vk, tap_vk = TRIGGER_VKS[settings["holdKey"]], KEY_NAMES[settings["tapKey"]]
                if not key_down(hold_vk):
                    self._set_status(f"等待按住 {settings['holdKey']}", holding=False)
                    next_tap = 0.0
                    self._wake.wait(POLL_SECONDS)
                    continue
                if not self._in_game():
                    self._set_status("游戏窗口不在前台，暂不连发", holding=False)
                    next_tap = 0.0
                    self._wake.wait(POLL_SECONDS)
                    continue
                now = time.perf_counter()
                if now < next_tap:
                    self._wake.wait(min(POLL_SECONDS, next_tap - now))
                    continue
                # Record before the call: an exception leaves delivery uncertain.
                with self._lock:
                    if (self._stop.is_set() or not self.settings["enabled"]
                            or self._generation != generation):
                        continue
                    self._pending_releases[tap_vk] = 0
                error = _send_key(tap_vk, up=False)
                if error:
                    with self._lock:
                        self._pending_releases.pop(tap_vk, None)  # SendInput inserted zero events.
                    self._fault(f"发送按键失败（Windows {error}）；开箱连点已停用。")
                    continue
                try:
                    deadline = now + min(PRESS_MS, settings["intervalMs"] / 2) / 1000
                    while (time.perf_counter() < deadline and self._active(generation)
                           and key_down(hold_vk) and self._in_game()):
                        self._wake.wait(min(POLL_SECONDS, max(0, deadline - time.perf_counter())))
                        self._wake.clear()
                finally:
                    self._release_pending()
                next_tap = now + settings["intervalMs"] / 1000 if self._active(generation) else 0.0
                self._set_status(f"正在连发 {settings['tapKey']}，松开 {settings['holdKey']} 停止",
                                 holding=self._active(generation))
            except Exception as error:
                self._fault(f"开箱连点异常，已停用：{type(error).__name__}: {error}")
                # The next iteration handles any uncertain or outstanding press.
