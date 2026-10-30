"""Pass-through native key events; management shortcuts do not need a game.

Production uses WH_KEYBOARD_LL, never RegisterHotKey (G stays with the game).
The old pollOnce seam remains for injected fake-native diagnostic tests only.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

from client_paths import APP_ROOT
APP = APP_ROOT
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from hotkey_bindings import Chord, modifiers_match, parse_chord, primary_state, validate_unique
from hotkey_keys import _primary_hotkey_to_vk
from session_state_machine import global_key_is_allowed


ACTION_ORDER = ("toggle-panel", "candidate-prev", "candidate-next", "confirm-round",
                "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round")
MANAGEMENT_ACTIONS = frozenset({"toggle-panel", "toggle-sheet-visibility", "start-round",
                                "toggle-round", "end-round"})
KNOWN_ACTIONS = frozenset(("map", "escape", *ACTION_ORDER))


class NativeWindowAPI:
    """Lazy native API seam shared with capture; construction performs no IO."""
    def __init__(self):
        self._user = None

    def _api(self):
        if self._user is None:
            if os.name != "nt":
                raise OSError("Native input requires Windows")
            user = ctypes.WinDLL("user32", use_last_error=True)
            signatures = (("IsWindow", [wintypes.HWND], wintypes.BOOL),
                          ("GetForegroundWindow", [], wintypes.HWND),
                          ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
                          ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD))
            for name, arguments, result in signatures:
                function = getattr(user, name)
                function.argtypes, function.restype = arguments, result
            self._user = user
        return self._user

    def is_window(self, hwnd):
        return bool(self._api().IsWindow(hwnd))

    def foreground_window(self):
        return int(self._api().GetForegroundWindow() or 0)

    def key_state(self, vk):
        return int(self._api().GetAsyncKeyState(vk)) & 0xffff

    def window_process_id(self, hwnd):
        process = wintypes.DWORD()
        if not self._api().GetWindowThreadProcessId(hwnd, ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(process.value)

    def monitor_device(self, hwnd):
        class Rect(ctypes.Structure):
            _fields_=[(name,ctypes.c_long) for name in ("left","top","right","bottom")]
        class Monitor(ctypes.Structure):
            _fields_=[("cbSize",wintypes.DWORD),("monitor",Rect),("work",Rect),
                      ("flags",wintypes.DWORD),("device",wintypes.WCHAR*32)]
        user=self._api()
        user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
        user.MonitorFromWindow.restype=wintypes.HANDLE
        user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Monitor)]
        user.GetMonitorInfoW.restype=wintypes.BOOL
        info=Monitor();info.cbSize=ctypes.sizeof(info)
        handle=user.MonitorFromWindow(hwnd,2)
        if not handle or not user.GetMonitorInfoW(handle,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.device


class NativeShortcutBackend(QObject):
    changed = Signal()
    actionTriggered = Signal(str)

    def __init__(self, native=None, parent=None, *, source=None):
        super().__init__(parent)
        self._native = native if native is not None else NativeWindowAPI()
        if source is None and native is None:
            from client_native_keys import PassThroughKeyboardSource
            source = PassThroughKeyboardSource(self)
        self._source = source
        self._management_enabled = False
        self._event_down = set()
        if source is not None:
            source.eventReceived.connect(self._key_event, Qt.QueuedConnection)
            source.started.connect(self._hook_started, Qt.QueuedConnection)
            source.failed.connect(self._hook_failed, Qt.QueuedConnection)
        self._hwnd = 0
        self._bindings = {}
        self._down = {}
        self._enabled = False
        self._closed = False
        self._prime = True
        self._game_focus = None
        self._text = False
        self._editing = False
        self._status = "disabled"
        self._message = "Native shortcuts are disabled."
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self.pollOnce)

    enabled = Property(bool, lambda self: self._enabled, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    snapshot = Property("QVariantMap", lambda self: dict(
        enabled=self._enabled, status=self._status, message=self._message,
        hwnd=str(self._hwnd), bindings={name: chord.name for name, chord in self._bindings.items()},
        textEntryActive=self._text, editingBindings=self._editing,
        readPermission="unknown", passThrough=True, globalRegistration=False,
        closed=self._closed, managementEnabled=self._management_enabled,
        hookRunning=bool(self._source and self._source.running),
        eventSource="pass-through-hook" if self._source is not None else "injected-poll-fixture"), notify=changed)

    def _stop(self, status, message):
        self._timer.stop()
        self._enabled = False
        self._status, self._message = status, message
        self._prime, self._game_focus = True, None
        self.changed.emit()

    @Slot("qlonglong")
    def bindWindow(self, hwnd):
        self.disable()
        if self._closed:
            return
        self._hwnd = int(hwnd)
        if self._hwnd <= 0:
            self._hwnd = 0
            self._stop("unknown", "An explicit game HWND is required.")

    @Slot("QVariantMap", result=bool)
    def configure(self, bindings):
        self.disable()
        if self._closed:
            return False
        management_requested = self._management_enabled
        self._bindings, self._down = {}, {}
        self._management_enabled = False
        if set(bindings) - KNOWN_ACTIONS:
            self._stop("unknown", "Unknown shortcut actions: " + ", ".join(sorted(set(bindings) - KNOWN_ACTIONS)))
            return False
        try:
            chords = {name: (Chord("ESCAPE", 0x1b) if name == "escape" and str(value).upper() in ("ESC", "ESCAPE")
                             else parse_chord(value, _primary_hotkey_to_vk))
                      for name, value in bindings.items()}
            # Legacy Escape is a fixed game-map close edge, observed separately
            # from configurable actions and never consumed from the game.
            chords.setdefault("escape", Chord("ESCAPE", 0x1b))
            validate_unique(chords)
        except (TypeError, ValueError, AttributeError) as error:
            self._stop("conflict", str(error))
            return False
        self._bindings = chords
        self._management_enabled = management_requested
        if self._source is not None:
            self._source.update_keys(chord.vk for chord in chords.values() if chord.vk)
            self._event_down = set(self._source.held)
        self._down = {name: False for name in chords}
        self._status, self._message = "disabled", "Bindings validated; native shortcuts remain disabled."
        self.changed.emit()
        return True

    @Slot("QVariantMap", result=bool)
    def configureSettings(self, settings):
        actions = settings.get("actionHotkeys", [])
        if len(actions) != len(ACTION_ORDER):
            self._bindings, self._down = {}, {}
            self._stop("conflict", "Shortcut action count does not match the product contract.")
            return False
        return self.configure(dict(map=settings.get("hotkey", ""), **dict(zip(ACTION_ORDER, actions))))

    @Slot(result=bool)
    def enable(self):
        if self._closed:
            return False
        if self._status in ("conflict", "unknown"):
            return False
        if not self._bindings or not any(chord.vk for chord in self._bindings.values()):
            self._stop("unknown", "Configure at least one shortcut before enabling.")
            return False
        try:
            if not self._hwnd or not self._native.is_window(self._hwnd):
                self._stop("unknown", "The explicitly bound game window is unavailable.")
                return False
        except Exception as error:
            self._stop("failed", f"Native shortcuts unavailable: {error}")
            return False
        self._prime, self._game_focus = True, None
        self._enabled, self._status = True, "ready"
        self._message = "Pass-through native observer enabled; input permission is unknown."
        if self._source is None:
            self._timer.start()  # Injected diagnostic API only; never production.
        elif not self._source.start():
            self._stop("failed", "Native keyboard events are unavailable.")
            return False
        self.changed.emit()
        return True

    @Slot(result=bool)
    def enableManagement(self):
        if self._closed or not self._bindings or self._status in ("conflict", "unknown"):
            return False
        if self._source is None:
            return False  # No production polling fallback.
        self._management_enabled = bool(self._source.start())
        self.changed.emit()
        return self._management_enabled

    def _hook_started(self, held):
        self._event_down.update(held)
        self.changed.emit()

    def _hook_failed(self, error):
        self._management_enabled = False
        self._stop("failed", "Native keyboard observer failed: " + error)

    def _key_event(self, event):
        if self._closed or self._source is None or event.get("generation") != self._source.generation:
            return
        vk, down = event["vk"], event["down"]
        previous = vk in self._event_down
        if down:self._event_down.add(vk)
        else:self._event_down.discard(vk)
        if not down or previous or self._text or self._editing:
            return
        held = event["held"]
        def read(key):
            pairs={0x10:(0x10,0xa0,0xa1),0x11:(0x11,0xa2,0xa3),0x12:(0x12,0xa4,0xa5)}
            return 0x8000 if any(k in held for k in pairs.get(key,(key,))) else 0
        foreground = event["foreground"]
        try:
            game = bool(self._hwnd and self._native.is_window(self._hwnd)
                and global_key_is_allowed(self._hwnd,foreground)
                and self._native.foreground_window() == foreground)
        except OSError:
            game = False  # A lost game must not disable global management keys.
        try:
            assistant = bool(foreground and self._native.window_process_id(foreground) == os.getpid())
        except OSError:
            assistant = False
        try:
            for action,chord in self._bindings.items():
                if chord.vk != vk or not modifiers_match(chord,read):continue
                if action in MANAGEMENT_ACTIONS:
                    allowed=self._management_enabled
                else:
                    allowed=self._enabled and game
                # The existing app-local QML shortcut owns F9 when this app is
                # foreground. The hook owns it globally when hidden/background.
                if action == "toggle-panel" and assistant:allowed=False
                if allowed:self.actionTriggered.emit(action)
        except Exception as error:
            self._hook_failed(str(error))

    @Slot()
    def disable(self):
        self._stop("disabled", "Native shortcuts are disabled.")

    @Slot(bool)
    def setTextEntryActive(self, active):
        if self._text != bool(active):
            self._text, self._prime = bool(active), True
            self.changed.emit()

    @Slot(bool)
    def setEditingBindings(self, active):
        if self._editing != bool(active):
            self._editing, self._prime = bool(active), True
            self.changed.emit()

    @Slot(result="QVariantList")
    def pollOnce(self):
        if not self._enabled or self._closed:
            return []
        try:
            if not self._native.is_window(self._hwnd):
                self._stop("unknown", "Bound game window was destroyed; explicit rebind is required.")
                return []
            foreground = self._native.foreground_window()
            game_focus = global_key_is_allowed(self._hwnd, foreground)
            assistant_focus = bool(foreground and self._native.window_process_id(foreground) == os.getpid())
            samples = {}
            def read(vk):
                if vk not in samples:
                    samples[vk] = int(self._native.key_state(vk)) & 0xffff
                return samples[vk]
            for vk in (0x10, 0x11, 0x12, 0x5b, 0x5c):
                read(vk)
            emitted = []
            suppress = self._prime or self._text or self._editing
            game_rearm = game_focus != self._game_focus
            for action, chord in self._bindings.items():
                state = primary_state(chord, read)
                down = bool(state & 0x8000)
                previous = self._down.get(action, False)
                # Same high-bit edge + low-bit short-press recovery as the
                # legacy async_key_edge, with one sample shared per VK/tick.
                edge = not previous and bool(state & 0x8001)
                self._down[action] = down
                allowed = action in MANAGEMENT_ACTIONS or game_focus
                if action == "toggle-panel" and assistant_focus:
                    allowed = False  # Application-local shortcut owns this edge.
                if action not in MANAGEMENT_ACTIONS and game_rearm:
                    allowed = False  # Held keys cannot fire on game focus return.
                if (allowed and not suppress and edge
                        and modifiers_match(chord, read, reject_released=True)):
                    emitted.append(action)
            self._prime, self._game_focus = False, game_focus
            for action in emitted:
                if not self._enabled or self._closed:
                    break
                self.actionTriggered.emit(action)
            return emitted
        except Exception as error:
            self._stop("failed", f"Native shortcut polling failed: {error}")
            return []

    @Slot()
    def shutdown(self):
        self.disable()
        self._management_enabled = False
        self._closed = True
        self._hwnd = 0
        if self._source is not None:
            self._source.stop()
        self.changed.emit()
