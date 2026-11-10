"""Best-effort Windows startup feedback, independent of Qt and product resources.

The native window belongs to a short-lived UI thread, so imports and resource
validation cannot starve its message pump. It has no topmost flag, input hooks,
settings, or single-instance ownership. Closing it never quits the application.
"""
import os
import threading
import time
from qt_startup_state import STATE


def _window_dpi(user, hwnd):
    import ctypes
    from ctypes import wintypes as w
    query = getattr(user, "GetDpiForWindow", None)
    if query is None:
        return 96
    query.argtypes = [w.HWND]
    query.restype = w.UINT
    value = query(hwnd)
    return value if isinstance(value, int) and value > 0 else 96


class StartupSplash:
    def __init__(self):
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None

    def show(self):
        if os.name != "nt" or self._thread is not None or self._stop.is_set():
            return
        try:
            self._thread = threading.Thread(target=self._run, name="startup-hint", daemon=True)
            self._thread.start()
            # Bounded handshake; a failed hint must never block normal startup.
            self._ready.wait(0.2)
        except Exception:
            self._stop.set()

    def close(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(0.5)
            except RuntimeError:
                pass

    def _run(self):
        try:
            self._native_window()
        except Exception:
            # This optional surface must not obscure the real startup failure.
            pass
        finally:
            self._ready.set()

    def _native_window(self):
        import ctypes
        from ctypes import wintypes as w
        user = ctypes.WinDLL("user32", use_last_error=True)
        # This auxiliary thread owns its awareness; never change Qt's process
        # DPI policy. The frozen application's manifest already requests PMv2.
        awareness = getattr(user, "SetThreadDpiAwarenessContext", None)
        previous = None
        if awareness is not None:
            awareness.argtypes = [w.HANDLE]
            awareness.restype = w.HANDLE
            previous = awareness(ctypes.c_void_p(-4))
        try:
            self._create_window(user)
        finally:
            if previous:
                awareness(previous)

    def _create_window(self, user):
        import ctypes
        from ctypes import wintypes as w
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        # Explicit pointer-sized signatures are essential in 64-bit builds.
        user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        user.CreateWindowExW.restype = w.HWND
        user.DestroyWindow.argtypes = [w.HWND]
        user.DestroyWindow.restype = w.BOOL
        user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user.UpdateWindow.argtypes = [w.HWND]
        user.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
        user.SetWindowTextW.restype = w.BOOL
        user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        user.PeekMessageW.restype = w.BOOL
        user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.restype = ctypes.c_ssize_t
        user.GetSystemMetrics.argtypes = [ctypes.c_int]
        user.GetSystemMetrics.restype = ctypes.c_int
        user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, w.UINT]
        user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t
        gdi.CreateFontW.argtypes = [ctypes.c_int] * 5 + [w.DWORD] * 8 + [w.LPCWSTR]
        gdi.CreateFontW.restype = w.HFONT
        gdi.DeleteObject.argtypes = [w.HGDIOBJ]
        gdi.DeleteObject.restype = w.BOOL
        if self._stop.is_set():
            return
        width, height = 440, 160
        screen_width, screen_height = user.GetSystemMetrics(0), user.GetSystemMetrics(1)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        # STATIC is a built-in class: no custom callback or Qt DLL is required.
        # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE; WS_POPUP | WS_BORDER.
        hwnd = user.CreateWindowExW(0x08000080, "STATIC", "", 0x80800000,
                                    x, y, width, height, None, None, None, None)
        if not hwnd:
            return
        font = None
        try:
            dpi = _window_dpi(user, hwnd)
            scale = lambda value: max(1, round(value * dpi / 96))
            width, height = scale(440), scale(160)
            user.SetWindowPos(hwnd, None, max(0, (screen_width - width) // 2),
                              max(0, (screen_height - height) // 2), width, height, 0x14)
            font = gdi.CreateFontW(-scale(16), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0,
                                   "Microsoft YaHei UI")
            labels = []
            for text, top in ((STATE.label(), 22), ("当前阶段耗时无法预计，无需重复点击。", 58)):
                label = user.CreateWindowExW(0, "STATIC", text, 0x50000001,
                                             scale(12), scale(top), width - scale(24), scale(28), hwnd, None, None, None)
                if not label:
                    return
                if font:
                    user.SendMessageW(label, 0x30, font, 1)  # WM_SETFONT
                labels.append(label)
            # A moving segment means unknown progress, never a fabricated
            # percentage. Built-in STATIC classes work without ComCtl v6/Qt.
            track_width, segment_width = scale(400), scale(80)
            track = user.CreateWindowExW(0, "STATIC", "", 0x50000012,
                scale(20), scale(106), track_width, scale(8), hwnd, None, None, None)
            segment = user.CreateWindowExW(0, "STATIC", "", 0x50000004,
                0, 0, segment_width, scale(8), track, None, None, None) if track else None
            if self._stop.is_set():
                return
            user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            user.UpdateWindow(hwnd)
            self._ready.set()
            message = w.MSG()
            previous_label = STATE.label()
            animation_start = time.monotonic()
            while not self._stop.is_set():
                while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user.TranslateMessage(ctypes.byref(message))
                    user.DispatchMessageW(ctypes.byref(message))
                label = STATE.label()
                if label != previous_label:
                    user.SetWindowTextW(labels[0], label)
                    previous_label = label
                if segment:
                    phase = ((time.monotonic()-animation_start) % 2.) / 2.
                    position = round((1. - abs(2. * phase - 1.)) * (track_width-segment_width))
                    user.SetWindowPos(segment, None, position, 0, segment_width, scale(8), 0x14)
                self._stop.wait(0.03)
        finally:
            user.DestroyWindow(hwnd)
            if font:
                gdi.DeleteObject(font)


_splash = StartupSplash()


def is_quiet_startup_args(argv):
    # CLI validation/help remain genuinely window-free, including packaged probes.
    # argparse also accepts unambiguous long-option abbreviations.
    quiet = ("--self-check", "--help", "--probe-output")
    for arg in argv:
        if arg == "--":
            break
        if arg == "-h" or (arg.startswith("--")
                and any(option.startswith(arg.split("=", 1)[0]) for option in quiet)):
            return True
    return False


def show_startup_splash(argv):
    if not is_quiet_startup_args(argv):
        _splash.show()


def close_startup_splash():
    _splash.close()


def set_startup_stage(stage):
    STATE.mark(stage)
