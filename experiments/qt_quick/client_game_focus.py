"""Explicit-click Qt facade over the original bound-game focus service."""
from __future__ import annotations

from copy import deepcopy
import sys

from PySide6.QtCore import QObject, Property, Signal, Slot
from client_paths import APP_ROOT

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from game_window_focus import NativeGameFocusAPI, focus_bound_game


class GameFocusService(QObject):
    changed = Signal()

    def __init__(self, get_bound_hwnd, *, is_closed=None, native=None, log=None, parent=None):
        super().__init__(parent)
        self._get_bound_hwnd = get_bound_hwnd
        self._is_closed = is_closed or (lambda: False)
        self._native = native if native is not None else NativeGameFocusAPI()
        self._log = log
        self._closed = False
        self._result = {}

    result = Property("QVariantMap", lambda self: deepcopy(self._result), notify=changed)
    message = Property(str, lambda self: str(self._result.get("message", "")), notify=changed)

    @Slot(result="QVariantMap")
    def requestFocus(self):
        """Call synchronously from the user's Return to Game click only."""
        native = self._native
        self._result = focus_bound_game(
            get_bound_hwnd=self._get_bound_hwnd,
            is_closed=lambda: self._closed or self._is_closed(),
            get_user32=native.get_user32,
            window_process_id=native.window_process_id,
            window_text=native.window_text,
            process_image_path=native.process_image_path,
            identity_is_allowed=native.identity_is_allowed,
            log=self._log,
        )
        self.changed.emit()
        return deepcopy(self._result)

    @Slot()
    def shutdown(self):
        self._closed = True
