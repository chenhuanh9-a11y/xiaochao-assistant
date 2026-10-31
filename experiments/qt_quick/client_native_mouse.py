"""Asynchronous Win32 mouse observation; never in the game's input hook chain.

Register only mouse INPUTSINK on an invisible message-only window. Do not use
NOLEGACY, CAPTUREMOUSE, pointer injection or device-relative deltas for display
translation. Physical cursor samples retain the old Tk observer's semantics.
https://learn.microsoft.com/windows/win32/inputdev/about-raw-input
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-registerrawinputdevices
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import time

from client_paths import APP_ROOT  # establishes the shared app-module path
from floor_observation_policy import MANUAL_FLOOR_HOLD_SECONDS


class RawInputDevice(ctypes.Structure):
    _fields_ = [("usagePage", W.USHORT), ("usage", W.USHORT),
                ("flags", W.DWORD), ("target", W.HWND)]


class RawInputHeader(ctypes.Structure):
    _fields_ = [("kind", W.DWORD), ("size", W.DWORD),
                ("device", W.HANDLE), ("wparam", W.WPARAM)]


class _Buttons(ctypes.Structure):
    _fields_ = [("flags", W.USHORT), ("data", W.USHORT)]


class _ButtonUnion(ctypes.Union):
    _fields_ = [("value", W.ULONG), ("buttons", _Buttons)]


class RawMouse(ctypes.Structure):
    _fields_ = [("flags", W.USHORT), ("buttonUnion", _ButtonUnion),
                ("rawButtons", W.ULONG), ("lastX", W.LONG),
                ("lastY", W.LONG), ("extra", W.ULONG)]


def mouse_phases(mouse):
    """Device counts indicate activity only; never reinterpret them as pixels."""
    buttons = mouse.buttonUnion.buttons.flags
    phases = []
    if buttons & 1:
        phases.append("down")
    elif mouse.lastX or mouse.lastY or mouse.flags & 1:
        phases.append("move")
    if buttons & 2:
        phases.append("up")
    return phases


class RawMouseObserver:
    WM_INPUT = 0x00ff
    INPUTSINK = 0x00000100
    REMOVE = 0x00000001

    def __init__(self, receive, relevant, *, user=None, cancel=None, clock=time.monotonic, ticks=None):
        self.user = user or ctypes.WinDLL("user32", use_last_error=True)
        self.receive, self.relevant = receive, relevant
        self.cancel, self.clock = cancel or (lambda reason: None), clock
        if ticks is None:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetTickCount64.argtypes = []
            kernel.GetTickCount64.restype = ctypes.c_ulonglong
            ticks = kernel.GetTickCount64
        self.ticks = ticks
        self.hwnd = None
        self.registered = False
        user = self.user
        signatures = (
            ("CreateWindowExW", [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
            ("DestroyWindow", [W.HWND], W.BOOL),
            ("DefWindowProcW", [W.HWND, W.UINT, W.WPARAM, W.LPARAM], ctypes.c_ssize_t),
            ("RegisterRawInputDevices", [ctypes.POINTER(RawInputDevice), W.UINT, W.UINT], W.BOOL),
            ("GetRegisteredRawInputDevices", [ctypes.POINTER(RawInputDevice), ctypes.POINTER(W.UINT), W.UINT], W.UINT),
            ("GetRawInputData", [W.HANDLE, W.UINT, W.LPVOID, ctypes.POINTER(W.UINT), W.UINT], W.UINT),
            ("GetThreadDpiAwarenessContext", [], W.HANDLE),
            ("GetAwarenessFromDpiAwarenessContext", [W.HANDLE], ctypes.c_int),
            ("GetForegroundWindow", [], W.HWND),
        )
        for name, arguments, result in signatures:
            function = getattr(user, name)
            function.argtypes, function.restype = arguments, result

    def start(self):
        if self.registered:
            return
        # QGuiApplication establishes per-monitor awareness before this worker
        # starts. MSG.pt is then physical screen pixels (Microsoft's PM contract).
        # A 0x0 message window cannot be used for logical-point conversion.
        # Verify, never change, the observer thread's coordinate context.
        awareness = self.user.GetAwarenessFromDpiAwarenessContext(self.user.GetThreadDpiAwarenessContext())
        if awareness != 2:
            raise OSError("Mouse observation requires the application's per-monitor DPI context")
        count = W.UINT()
        size = ctypes.sizeof(RawInputDevice)
        if self.user.GetRegisteredRawInputDevices(None, ctypes.byref(count), size) == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        if count.value:
            devices = (RawInputDevice * count.value)()
            read = self.user.GetRegisteredRawInputDevices(devices, ctypes.byref(count), size)
            if read == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            if any(row.usagePage == 1 and row.usage == 2 for row in devices[:read]):
                raise OSError("Mouse raw-input observation already has an owner in this process")
        # The system STATIC class requires no custom WndProc/class lifetime.
        # HWND_MESSAGE is invisible and cannot become the foreground window.
        self.hwnd = self.user.CreateWindowExW(0, "STATIC", "", 0,
                                             0, 0, 0, 0, W.HWND(-3), None, None, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        device = RawInputDevice(1, 2, self.INPUTSINK, self.hwnd)
        if not self.user.RegisterRawInputDevices(ctypes.byref(device), 1, size):
            error = ctypes.WinError(ctypes.get_last_error())
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
            raise error
        self.registered = True

    def handle(self, message):
        if message.hwnd != self.hwnd or message.message != self.WM_INPUT:
            return False
        try:
            foreground = int(self.user.GetForegroundWindow() or 0)
            if not self.relevant(foreground):
                return True
            # MSG.time/pt describe enqueue time/location, not the later point
            # after Python resumes. Retain the existing input freshness budget.
            age = ((int(self.ticks()) & 0xffffffff) - int(message.time)) & 0xffffffff
            if age > MANUAL_FLOOR_HOLD_SECONDS * 1000:
                self.cancel("raw-mouse-input-expired")
                return True
            created_at = self.clock() - age / 1000.
            size, header_size = W.UINT(), ctypes.sizeof(RawInputHeader)
            if self.user.GetRawInputData(message.lParam, 0x10000003, None,
                                        ctypes.byref(size), header_size) == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            if not header_size + ctypes.sizeof(RawMouse) <= size.value <= 65536:
                return True
            buffer = ctypes.create_string_buffer(size.value)
            read = self.user.GetRawInputData(message.lParam, 0x10000003, buffer,
                                             ctypes.byref(size), header_size)
            if read == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            if read < header_size + ctypes.sizeof(RawMouse):
                return True
            header = RawInputHeader.from_buffer(buffer)
            if header.kind != 0 or header.size != read:
                return True
            mouse = RawMouse.from_buffer(buffer, header_size)
            point = W.POINT(message.pt.x, message.pt.y)
            for phase in mouse_phases(mouse):
                self.receive(point.x, point.y, foreground, phase=phase, created_at=created_at)
            return True
        finally:
            # Includes RIM_INPUT foreground cleanup, without dispatching input
            # to another window or holding the game's delivery path.
            self.user.DefWindowProcW(message.hwnd, message.message, message.wParam, message.lParam)

    def stop(self):
        errors = []
        if self.registered:
            device = RawInputDevice(1, 2, self.REMOVE, None)
            if not self.user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                errors.append("raw-input remove: " + str(ctypes.WinError(ctypes.get_last_error())))
            else:
                self.registered = False
        if self.hwnd:
            if not self.user.DestroyWindow(self.hwnd):
                errors.append("message-window destroy: " + str(ctypes.WinError(ctypes.get_last_error())))
            else:
                self.hwnd = None
        if errors:
            raise OSError("; ".join(errors))
