"""Optional Windows UI sounds; same aliases, flags and throttle as the Tk shell."""
from __future__ import annotations

import time
from PySide6.QtCore import QObject


def play_system_sound(alias):
    try:
        import winsound
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT | winsound.SND_NOSTOP)
    except (ImportError, RuntimeError, OSError):
        pass


class UiFeedback(QObject):
    def __init__(self, *, settings, windows, session, coordinator, enabled=True,
                 player=play_system_sound, clock=time.monotonic):
        super().__init__(coordinator)
        self.settings, self.windows = settings, windows
        self.session, self.coordinator = session, coordinator
        self._allowed, self._player, self._clock = enabled, player, clock
        self._enabled = bool(settings.persistedValues.get("uiSoundEnabled", False))
        self._last = float("-inf")
        self._confirm_pending = False
        self._collapsed = self._collapsed_state()
        settings.saved.connect(self._saved)
        windows.windowChanged.connect(self._window_changed)
        coordinator.controlVisibilityChanged.connect(self._controls_changed)
        session.changed.connect(self._session_changed)

    def _collapsed_state(self):
        return {name: bool(row.get("collapsed")) for name, row in self.windows.snapshot.items()}

    def play(self, kind):
        if (not self._allowed or not self._enabled
                or self.coordinator.closing or self.coordinator._closed):
            return
        now = self._clock()
        if now - self._last < .16:
            return
        self._last = now
        self._player("SystemAsterisk" if kind in {"expand", "confirm"} else "SystemDefault")

    def _saved(self):
        previous = self._enabled
        self._enabled = bool(self.settings.persistedValues.get("uiSoundEnabled", False))
        if self._enabled and not previous:
            self.play("confirm")

    def _window_changed(self, name):
        if name != "controls":
            return
        row = self.windows.snapshot.get(name)
        if row is None:
            self._collapsed.pop(name, None)
            return
        collapsed = bool(row.get("collapsed"))
        previous = self._collapsed.get(name)
        self._collapsed[name] = collapsed
        if previous is not None and collapsed != previous:
            self.play("collapse" if collapsed else "expand")

    def _controls_changed(self, visible, source):
        self.play("expand" if visible else "collapse")

    def _session_changed(self):
        state = self.session.snapshot
        if state.get("busy"):
            self._confirm_pending = state.get("pendingAction") == "confirm_route"
            return
        pending, self._confirm_pending = self._confirm_pending, False
        receipt = state.get("lastReceipt") or {}
        if pending and receipt.get("action") == "confirm_route" and receipt.get("success") is True:
            self.play("confirm")
