"""Lifecycle for explicitly registered Qt windows; no foreign HWND access/hooks."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
import sys

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QWindow

from client_paths import APP_ROOT
APP = APP_ROOT
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from hotkey_bindings import parse_chord, validate_unique


WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020


def overlay_extended_style(style, click_through=False):
    """Production-compatible pure NOACTIVATE/TOOLWINDOW/TRANSPARENT mapping."""
    style = int(style) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    return style | WS_EX_TRANSPARENT if click_through else style & ~WS_EX_TRANSPARENT


def _native_overlay_style(window, click_through):
    if (os.name != "nt" or not isinstance(window, QWindow)
            or QGuiApplication.platformName() in ("offscreen", "minimal")):
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user.SetWindowLongW.restype = wintypes.LONG
    # winId comes exclusively from a Qt window registered by this controller.
    handle = int(window.winId())
    ctypes.set_last_error(0)
    style = user.GetWindowLongW(handle, -20)
    if not style and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.set_last_error(0)
    previous = user.SetWindowLongW(handle, -20, overlay_extended_style(style, click_through))
    if not previous and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())


class DisabledGlobalHotkeyBackend:
    """Explicit seam only. QML Shortcut/QShortcut remain application-local.

    A future native backend must provide its own opt-in lifecycle and foreground
    policy. This backend never hooks keys or calls RegisterHotKey.
    """
    enabled = False
    reason = "Native global shortcuts are disabled; Qt shortcuts are application-local."

    def configure(self, bindings, resolve_primary):
        chords = {name: parse_chord(value, resolve_primary) for name, value in bindings.items()}
        validate_unique(chords)
        return chords

    def start(self):
        return False

    def stop(self):
        pass


class WindowController(QObject):
    changed = Signal()
    windowChanged = Signal(str)

    # Management surfaces can accept focus; every other registered surface is
    # a passive assistant tool and never explicitly activates the foreground.
    FOCUSABLE = frozenset({"main", "settings", "resources", "support"})

    def __init__(self, parent=None):
        super().__init__(parent)
        self._windows = {}
        self._state = {}
        self._message = ""
        self.globalHotkeys = DisabledGlobalHotkeyBackend()

    message = Property(str, lambda self: self._message, notify=changed)
    snapshot = Property("QVariantMap", lambda self: {name: dict(value) for name, value in self._state.items()}, notify=changed)

    def _window(self, name):
        window = self._windows.get(name)
        if window is None:
            self._message = f"Unregistered Qt window: {name}"
            self.changed.emit()
        return window

    def _notify(self, name):
        self.windowChanged.emit(name)
        self.changed.emit()

    @Slot(str, QObject)
    def bindWindow(self, name, window):
        if not name or not isinstance(window, QObject):
            raise ValueError("A named Qt window is required")
        if not all(hasattr(window, method) for method in ("show", "hide", "setFlags", "flags", "setOpacity")):
            raise TypeError("Only Qt window objects can be registered")
        existing = self._windows.get(name)
        if existing is not None and existing is not window:
            raise ValueError(f"Qt window already registered: {name}")
        if existing is window:
            return
        self._windows[name] = window
        self._state[name] = dict(visible=bool(window.isVisible()), clickThrough=False,
                                 opacity=float(window.opacity()), collapsed=False)
        window.destroyed.connect(self._window_destroyed)
        if hasattr(window, "visibleChanged"):
            window.visibleChanged.connect(self._visibility_changed)
        if name not in self.FOCUSABLE:
            window.setFlags(window.flags() | Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
            _native_overlay_style(window, False)
        self._notify(name)

    def _unbind(self, name):
        self._windows.pop(name, None)
        self._state.pop(name, None)
        self._notify(name)

    @Slot(QObject)
    def _window_destroyed(self, window):
        for name, registered in tuple(self._windows.items()):
            if registered is window or registered == window:
                self._unbind(name)

    @Slot(bool)
    def _visibility_changed(self, visible):
        window = self.sender()
        for name, registered in self._windows.items():
            if registered is window:
                self._state[name]["visible"] = bool(visible)
                self._notify(name)
                return

    @Slot(str)
    def show(self, name):
        window = self._window(name)
        if window is None:
            return
        window.show()
        if name not in self.FOCUSABLE:
            _native_overlay_style(window, self._state[name]["clickThrough"])
        self._state[name]["visible"] = True
        self._notify(name)

    @Slot(str)
    def hide(self, name):
        window = self._window(name)
        if window is not None:
            window.hide()
            self._state[name]["visible"] = False
            self._notify(name)

    @Slot(str, bool)
    def setClickThrough(self, name, enabled):
        window = self._window(name)
        if window is None:
            return
        if name in self.FOCUSABLE:
            self._message = "Click-through is available on assistant overlays only."
            self.changed.emit()
            return
        visible = window.isVisible()
        flags = window.flags()
        flags = flags | Qt.WindowTransparentForInput if enabled else flags & ~Qt.WindowTransparentForInput
        window.setFlags(flags)
        _native_overlay_style(window, enabled)
        if visible:
            window.show()
        self._state[name]["clickThrough"] = bool(enabled)
        self._notify(name)

    @Slot(str, float)
    def setOpacity(self, name, opacity):
        window = self._window(name)
        if window is None:
            return
        value = float(opacity)
        if not math.isfinite(value):
            self._message = "Opacity must be a finite number."
            self.changed.emit()
            return
        value = min(1.0, max(0.05, value))
        window.setOpacity(value)
        self._state[name]["opacity"] = value
        self._notify(name)

    @Slot(str)
    def toggleCollapsed(self, name):
        window = self._window(name)
        if window is not None:
            collapsed = not self._state[name]["collapsed"]
            self._state[name]["collapsed"] = collapsed
            window.setProperty("collapsed", collapsed)
            self._notify(name)

    @Slot()
    def shutdown(self):
        self.globalHotkeys.stop()
        for name in tuple(self._windows):
            self.hide(name)
