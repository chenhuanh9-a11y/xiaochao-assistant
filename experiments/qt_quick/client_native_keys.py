"""Pass-through WH_KEYBOARD_LL source with its own Win32 message loop.

No hook is installed at import/construction. The callback never runs business
work or consumes input; it queues only subscribed key edges and always returns
CallNextHookEx. See Microsoft LowLevelKeyboardProc/SetWindowsHookExW docs.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import os
import threading
from PySide6.QtCore import QObject, Signal

MODIFIER_KEYS = frozenset((0x10,0x11,0x12,0x5b,0x5c,0xa0,0xa1,0xa2,0xa3,0xa4,0xa5))

class PassThroughKeyboardSource(QObject):
    eventReceived = Signal(object)
    started = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._keys, self._held = frozenset(), set()
        self._thread_id = 0
        self._user = None
        self._callback = None
        self.generation = 0
        self.installed = False

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def held(self):
        with self._lock:
            return frozenset(self._held)

    def update_keys(self, keys):
        with self._lock:
            self._keys = frozenset(keys) | MODIFIER_KEYS
            self.generation += 1

    def start(self):
        if self.running:
            return True
        if os.name != "nt":
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="qt-pass-through-keys", daemon=False)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread_id and self._user is not None:
            self._user.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(.5)
        return not self.running

    def _handle_message(self, message):
        """Win32 thread-message extension seam; production has no commands."""

    def _run(self):
        handle = None
        try:
            user = self._user = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            result = ctypes.c_ssize_t
            callback_type = ctypes.WINFUNCTYPE(result, ctypes.c_int, W.WPARAM, W.LPARAM)
            class Key(ctypes.Structure):
                _fields_ = [("vk",W.DWORD),("scan",W.DWORD),("flags",W.DWORD),("time",W.DWORD),("extra",ctypes.c_size_t)]
            user.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,W.HINSTANCE,W.DWORD]
            user.SetWindowsHookExW.restype=W.HANDLE
            user.CallNextHookEx.argtypes=[W.HANDLE,ctypes.c_int,W.WPARAM,W.LPARAM]
            user.CallNextHookEx.restype=result
            user.UnhookWindowsHookEx.argtypes=[W.HANDLE];user.UnhookWindowsHookEx.restype=W.BOOL
            user.GetForegroundWindow.argtypes=[];user.GetForegroundWindow.restype=W.HWND
            user.GetAsyncKeyState.argtypes=[ctypes.c_int];user.GetAsyncKeyState.restype=ctypes.c_short
            user.PeekMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
            user.PeekMessageW.restype=W.BOOL
            user.GetMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]
            user.GetMessageW.restype=ctypes.c_int
            user.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM]
            user.PostThreadMessageW.restype=W.BOOL
            kernel.GetCurrentThreadId.argtypes=[];kernel.GetCurrentThreadId.restype=W.DWORD
            kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HMODULE
            message=W.MSG()
            user.PeekMessageW(ctypes.byref(message),None,0,0,0)
            self._thread_id=int(kernel.GetCurrentThreadId())
            # One initial level snapshot suppresses keys held before subscribing;
            # key delivery thereafter is event-driven, never async-key polling.
            with self._lock:
                self._held={vk for vk in self._keys if user.GetAsyncKeyState(vk)&0x8000}
            @callback_type
            def callback(code, message_id, pointer):
                try:
                    if code >= 0 and not self._stop.is_set() and message_id in (0x100,0x101,0x104,0x105):
                        key=ctypes.cast(pointer,ctypes.POINTER(Key)).contents
                        down=message_id in (0x100,0x104)
                        with self._lock:
                            watched=key.vk in self._keys
                            if watched:
                                if down:self._held.add(key.vk)
                                else:self._held.discard(key.vk)
                                event=dict(vk=int(key.vk),down=down,held=frozenset(self._held),
                                    foreground=int(user.GetForegroundWindow() or 0),generation=self.generation)
                        if watched:
                            self.eventReceived.emit(event)
                except Exception:
                    # Input remains pass-through even if a consumer is gone.
                    pass
                return user.CallNextHookEx(None,code,message_id,pointer)
            self._callback=callback
            handle=user.SetWindowsHookExW(13,callback,kernel.GetModuleHandleW(None),0)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.installed=True
            self.started.emit(self.held)
            while not self._stop.is_set():
                value=user.GetMessageW(ctypes.byref(message),None,0,0)
                if value == -1:raise ctypes.WinError(ctypes.get_last_error())
                if value == 0:break
                self._handle_message(message)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}:{error}")
        finally:
            if handle and not self._user.UnhookWindowsHookEx(handle):
                self.failed.emit("Native keyboard hook could not be unregistered")
            self.installed=False
            self._thread_id=0
            self._callback=None
