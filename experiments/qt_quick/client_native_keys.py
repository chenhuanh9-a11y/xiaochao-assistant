"""Pass-through key and scoped left-click hooks on one Win32 message loop.

No hook is installed at import/construction. The callback never runs business
work or consumes input; it queues subscribed key edges and bound-game left
presses, and always returns CallNextHookEx. See Microsoft LowLevelKeyboardProc,
LowLevelMouseProc and SetWindowsHookExW documentation.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import os
import threading
import time
from PySide6.QtCore import QObject, Signal
from client_map_input import PointerEventMailbox

MODIFIER_KEYS = frozenset((0x10,0x11,0x12,0x5b,0x5c,0xa0,0xa1,0xa2,0xa3,0xa4,0xa5))


def initial_pressed_keys(keys, read):
    # Generic modifier state is the OR of its two physical sides. Seeding both
    # representations would leave one stuck after a single physical key-up.
    return {vk for vk in keys if vk not in (0x10, 0x11, 0x12) and read(vk) & 0x8000}


def physical_key(vk, scan, flags):
    if vk == 0x10:
        return 0xa1 if scan == 0x36 else 0xa0
    if vk == 0x11:
        return 0xa3 if flags & 1 else 0xa2
    if vk == 0x12:
        return 0xa5 if flags & 1 else 0xa4
    return vk

class PassThroughKeyboardSource(QObject):
    eventReceived = Signal(object)
    pointerReceived = Signal(object)
    pointerAvailable = Signal()
    pointerFailed = Signal(str)
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
        self._mouse_callback = None
        self._pointer_target = 0
        self._pointer_context_provider = None
        self.pointer_generation = 0
        self._pointer_sequence = 0
        self._pointer_gesture = None
        self._pointer_mailbox = PointerEventMailbox()
        self.pointer_installed = False
        self._pointer_failure_reported = False
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

    def set_pointer_context_provider(self, provider):
        # The provider only stamps cached metadata and must never block/perform
        # capture or business work on the native hook thread.
        with self._lock:
            self._pointer_context_provider = provider if callable(provider) else None
            self.pointer_generation += 1
        self.cancel_pointer("pointer-context-provider-changed")

    def update_pointer_target(self, hwnd):
        changed = False
        with self._lock:
            if self._pointer_target != int(hwnd):
                self._pointer_target = int(hwnd)
                self.pointer_generation += 1
                changed = True
        if changed:
            self.cancel_pointer("pointer-target-changed")

    def _pointer_event(self, x, y, foreground, *, created_at=None, phase="down"):
        with self._lock:
            target, provider, generation = self._pointer_target, self._pointer_context_provider, self.pointer_generation
            gesture = self._pointer_gesture
        if phase not in {"down", "move", "up"}:
            return None
        if not target or foreground != target or provider is None or self._stop.is_set():
            self.cancel_pointer("pointer-context-unavailable")
            return None
        if phase != "down" and gesture is None:
            return None
        created_at = time.monotonic() if created_at is None else created_at
        context = provider()
        if not isinstance(context, dict):
            self.cancel_pointer("pointer-context-unavailable")
            return None
        context = dict(context, createdAt=created_at)
        with self._lock:
            if generation != self.pointer_generation or target != self._pointer_target:
                return None
            self._pointer_sequence += 1
            sequence = self._pointer_sequence
            if phase == "down":
                gesture = dict(id=sequence, start=(int(x), int(y)), last=(int(x), int(y)))
            elif gesture is not self._pointer_gesture:
                return None
            last = gesture["last"]
            event = dict(x=int(x), y=int(y), hwnd=target, foreground=foreground, phase=phase,
                         createdAt=created_at, context=context, sequence=sequence, pointerGeneration=generation,
                         gestureId=gesture["id"], delta=[int(x)-last[0], int(y)-last[1]],
                         totalDelta=[int(x)-gesture["start"][0], int(y)-gesture["start"][1]])
            gesture["last"] = (int(x), int(y))
            gesture["event"] = event
            self._pointer_gesture = None if phase == "up" else gesture
        return event

    def cancel_pointer(self, reason="pointer-cancelled"):
        with self._lock:
            gesture, self._pointer_gesture = self._pointer_gesture, None
        if gesture is not None:
            self._queue_pointer_event(dict(gesture["event"], phase="cancel", reason=str(reason)))

    def _queue_pointer_event(self, event):
        if self._pointer_mailbox.post(event):
            self.pointerAvailable.emit()

    def take_pointer_events(self):
        return self._pointer_mailbox.take()

    def start(self):
        if self.running:
            return True
        if os.name != "nt":
            return False
        self._stop.clear()
        self._pointer_failure_reported = False
        self._thread = threading.Thread(target=self._run, name="qt-pass-through-keys", daemon=False)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        self.cancel_pointer("pointer-source-stopped")
        if self._thread_id and self._user is not None:
            self._user.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(.5)
        return not self.running

    def _transition_event(self, vk, scan, flags, down, foreground):
        with self._lock:
            if vk not in self._keys:
                return None
            key = physical_key(vk, scan, flags)
            if down:
                self._held.add(key)
            else:
                self._held.discard(key)
            # Preserve the existing vk protocol; only held-state storage uses
            # one physical representation for either callback form.
            return dict(vk=vk, down=down, held=frozenset(self._held), createdAt=time.monotonic(),
                        foreground=foreground, generation=self.generation)

    def _handle_message(self, message):
        """Win32 thread-message extension seam; production has no commands."""

    def _run(self):
        handle = None
        mouse_handle = None
        try:
            user = self._user = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            result = ctypes.c_ssize_t
            callback_type = ctypes.WINFUNCTYPE(result, ctypes.c_int, W.WPARAM, W.LPARAM)
            class Key(ctypes.Structure):
                _fields_ = [("vk",W.DWORD),("scan",W.DWORD),("flags",W.DWORD),("time",W.DWORD),("extra",ctypes.c_size_t)]
            class Mouse(ctypes.Structure):
                _fields_ = [("pt",W.POINT),("mouseData",W.DWORD),("flags",W.DWORD),
                            ("time",W.DWORD),("extra",ctypes.c_size_t)]
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
                self._held=initial_pressed_keys(self._keys, user.GetAsyncKeyState)
            @callback_type
            def callback(code, message_id, pointer):
                try:
                    if code >= 0 and not self._stop.is_set() and message_id in (0x100,0x101,0x104,0x105):
                        key=ctypes.cast(pointer,ctypes.POINTER(Key)).contents
                        down=message_id in (0x100,0x104)
                        if key.vk in self._keys:
                            event=self._transition_event(int(key.vk), int(key.scan), int(key.flags), down,
                                int(user.GetForegroundWindow() or 0))
                            if event is not None:
                                self.eventReceived.emit(event)
                except Exception:
                    # Input remains pass-through even if a consumer is gone.
                    pass
                return user.CallNextHookEx(None,code,message_id,pointer)
            self._callback=callback
            handle=user.SetWindowsHookExW(13,callback,kernel.GetModuleHandleW(None),0)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            @callback_type
            def mouse_callback(code, message_id, pointer):
                try:
                    if code >= 0 and message_id in (0x0200, 0x0201, 0x0202) and not self._stop.is_set():
                        foreground = int(user.GetForegroundWindow() or 0)
                        if foreground == self._pointer_target or self._pointer_gesture is not None:
                            mouse = ctypes.cast(pointer,ctypes.POINTER(Mouse)).contents
                            phase = {0x0200: "move", 0x0201: "down", 0x0202: "up"}[message_id]
                            event = self._pointer_event(mouse.pt.x, mouse.pt.y, foreground, phase=phase)
                            if event is not None:
                                self._queue_pointer_event(event)
                except Exception as error:
                    if not self._pointer_failure_reported:
                        self._pointer_failure_reported = True
                        self.pointerFailed.emit(f"Floor-click observation failed: {type(error).__name__}:{error}"[:512])
                return user.CallNextHookEx(None,code,message_id,pointer)
            self._mouse_callback = mouse_callback
            mouse_handle = user.SetWindowsHookExW(14,mouse_callback,kernel.GetModuleHandleW(None),0)
            self.pointer_installed = bool(mouse_handle)
            if not mouse_handle:
                self.pointerFailed.emit("Native floor-click observer could not be installed")
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
            if mouse_handle and not self._user.UnhookWindowsHookEx(mouse_handle):
                self.pointerFailed.emit("Native floor-click hook could not be unregistered")
            if handle and not self._user.UnhookWindowsHookEx(handle):
                self.failed.emit("Native keyboard hook could not be unregistered")
            self.installed=False
            self._thread_id=0
            self._callback=None
            self._mouse_callback=None
            self.pointer_installed=False
