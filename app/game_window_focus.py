"""One explicit request to focus the already-bound game; no discovery or hooks.

The controller supplies its live binding/closed state and native readers. This
is the legacy desktop-click sequence, separated from Tk without adding input
attachment, foreground retries, elevation, or process activation workarounds.
"""
from __future__ import annotations


def focus_bound_game(*, get_bound_hwnd, is_closed, get_user32,
                     window_process_id, window_text, process_image_path,
                     identity_is_allowed, log=None):
    hwnd = get_bound_hwnd()
    process_id = None
    restore_requested = False

    def finish(success, reason, message):
        result = dict(success=bool(success), reason=reason, message=message)
        if log is not None:
            log("desktop-game-focus", success=result["success"], reason=reason,
                gameWindowBound=bool(hwnd), gameProcessId=process_id,
                restoreRequested=restore_requested)
        return result

    if is_closed():
        return finish(False, "assistant-closed", "助手已关闭，未切换窗口")
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or not hwnd:
        return finish(False, "game-not-bound", "尚未绑定游戏，请先打开第五人格")

    def validate(user32):
        nonlocal process_id
        if is_closed():
            return "assistant-closed", "助手已关闭，未继续切换窗口"
        if get_bound_hwnd() != hwnd:
            return "binding-changed", "游戏窗口绑定已变化，请重新点击切回游戏"
        if not user32.IsWindow(hwnd):
            return "game-window-unavailable", "已绑定的游戏窗口已失效，请先打开游戏"
        current_pid = window_process_id(hwnd)
        if current_pid is None:
            return "identity-unavailable", "暂时无法核验游戏窗口，请手动切回游戏"
        if process_id is not None and current_pid != process_id:
            return "binding-changed", "游戏窗口已变化，请重新点击切回游戏"
        process_id = current_pid
        title = window_text(hwnd)
        image_path = process_image_path(current_pid)
        if not image_path:
            return "identity-unavailable", "无法读取游戏进程信息，请手动切回游戏"
        if not identity_is_allowed(title, image_path):
            return "identity-mismatch", "已绑定窗口不是第五人格，未切换窗口"
        if (is_closed() or get_bound_hwnd() != hwnd or not user32.IsWindow(hwnd)
                or window_process_id(hwnd) != process_id):
            return "binding-changed", "游戏窗口已变化，未继续切换窗口"
        return None

    try:
        user32 = get_user32()
        failure = validate(user32)
        if failure:
            return finish(False, *failure)
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return finish(True, "already-foreground", "游戏已在前台")
        if user32.IsIconic(hwnd):
            failure = validate(user32)
            if failure:
                return finish(False, *failure)
            restore_requested = True
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE, for this explicit click only.
        if user32.IsIconic(hwnd):
            return finish(False, "restore-not-confirmed", "游戏仍处于最小化状态，请手动切回游戏")
        failure = validate(user32)
        if failure:
            return finish(False, *failure)
        accepted = bool(user32.SetForegroundWindow(hwnd))
        failure = validate(user32)
        if failure:
            return finish(False, *failure)
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return finish(True, "focused", "已切回游戏")
        if not accepted:
            return finish(False, "foreground-denied", "系统未允许切回游戏，请手动点击游戏窗口")
        return finish(False, "foreground-not-confirmed", "游戏尚未成为前台窗口，请手动切回游戏")
    except (AttributeError, OSError, TypeError, ValueError):
        return finish(False, "focus-request-failed", "切回游戏失败，窗口可能已关闭或权限不足，请手动切回")


class NativeGameFocusAPI:
    """Lazy Windows implementation; constructor and import make no native calls."""
    def __init__(self):
        self._user32 = None

    def get_user32(self):
        if self._user32 is None:
            import ctypes
            from ctypes import wintypes
            import os
            if os.name != "nt":
                raise OSError("Returning to the game requires Windows")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            for name, arguments, result in (
                ("IsWindow", [wintypes.HWND], wintypes.BOOL),
                ("GetForegroundWindow", [], wintypes.HWND),
                ("IsIconic", [wintypes.HWND], wintypes.BOOL),
                ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
                ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
                ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ):
                function = getattr(user32, name)
                function.argtypes, function.restype = arguments, result
            self._user32 = user32
        return self._user32

    def window_process_id(self, hwnd):
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        self.get_user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None

    @staticmethod
    def window_text(hwnd):
        from identityv_live_mvp import window_text
        return window_text(hwnd)

    @staticmethod
    def process_image_path(pid):
        from identityv_live_mvp import process_image_path
        return process_image_path(pid)

    @staticmethod
    def identity_is_allowed(title, image_path):
        from identityv_live_mvp import identityv_window_identity_is_allowed
        return identityv_window_identity_is_allowed(title, image_path)
