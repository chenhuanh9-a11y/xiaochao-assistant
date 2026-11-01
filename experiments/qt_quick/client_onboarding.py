"""Qt consumer of the original two-step onboarding/save/control-trial protocol."""
from PySide6.QtCore import QObject, Property, Signal, Slot


class OnboardingService(QObject):
    changed = Signal()

    def __init__(self, *, settings, windows, coordinator, startup_enabled=True):
        super().__init__(coordinator)
        self.settings, self.windows, self.coordinator = settings, windows, coordinator
        self._active = startup_enabled and not settings.persistedValues.get("onboardingCompleted", False)
        self._step, self._message, self._pending = 1, "", ""
        self._saving_observed = False
        self._binding = self._control_binding()
        self._trial = dict(shown=False, hidden=False, shownSource="", hiddenSource="", mouseUsed=False)
        settings.saved.connect(self._saved)
        settings.changed.connect(self._settings_changed)
        windows.changed.connect(self.changed)
        coordinator.controlVisibilityChanged.connect(self._observe)
        coordinator.changed.connect(self._closing_changed)

    active = Property(bool, lambda s: s._active, notify=changed)
    step = Property(int, lambda s: s._step, notify=changed)
    message = Property(str, lambda s: s._message or s.settings.message if s._pending else s._message, notify=changed)

    def _control_binding(self):
        keys = self.settings.persistedValues.get("actionHotkeys") or []
        return keys[0] if keys else ""

    def _snapshot(self):
        visible = bool(self.windows.snapshot.get("controls", {}).get("visible"))
        available = not (self.coordinator.closing or self.settings.shortcutRecording or self.settings.busy)
        return dict(self._trial, panelVisible=visible, keyText=self._control_binding(),
                    canShow=available and not visible, canHide=available and visible,
                    keyboardReady=self._trial["shownSource"] == self._trial["hiddenSource"] == "keyboard",
                    canFinish=available and bool(self.settings.persistedValues.get("hotkey"))
                    and bool(self._control_binding()) and self._trial["shown"] and self._trial["hidden"])

    trial = Property("QVariantMap", _snapshot, notify=changed)

    @Slot()
    def open(self):
        if self.coordinator.closing or self.coordinator._closed:
            return
        self._active, self._step, self._message = True, 1, ""
        self.changed.emit()

    @Slot()
    def dismiss(self):
        self.settings.cancelShortcutCapture()
        self._active = False
        self.changed.emit()

    def _closing_changed(self):
        if self.coordinator.closing:
            self.dismiss()

    def _settings_changed(self):
        binding = self._control_binding()
        if binding != self._binding:
            self._binding = binding
            self._trial = dict(shown=False, hidden=False, shownSource="", hiddenSource="", mouseUsed=False)
        if self._pending and self.settings.busy:
            self._saving_observed = True
        elif self._pending and self._saving_observed:
            self._saving_observed = False
            self._message = self.settings.message
            fields = ["onboardingCompleted"] if self._pending == "finish" else ["hotkey", "actionHotkeys"]
            if any(self.settings.values.get(k) != self.settings.persistedValues.get(k) for k in fields):
                self._pending = ""
        self.changed.emit()

    def _observe(self, visible, source):
        if not self._active or self._step != 2 or self.settings.shortcutRecording or not self._control_binding():
            return
        if visible:
            self._trial.update(shown=True, shownSource=source)
        elif self._trial["shown"]:
            self._trial.update(hidden=True, hiddenSource=source)
        self._trial["mouseUsed"] |= source == "mouse"
        self.changed.emit()

    @Slot(bool)
    def tryControls(self, visible):
        snapshot = self._snapshot()
        if snapshot["canShow" if visible else "canHide"]:
            self.coordinator.toggleControls()

    @Slot()
    def back(self):
        if not self.settings.busy:
            self.settings.cancelShortcutCapture()
            self._step, self._message = 1, ""
            self.changed.emit()

    @Slot()
    def advance(self):
        if not self._active or self.settings.busy or self.coordinator.closing:
            return
        if self.settings.shortcutRecording:
            if not self.settings.confirmShortcutCapture():
                self._message = "请先完成录制，或取消本次修改。"
                self.changed.emit()
                return
        fields = ["hotkey", "actionHotkeys"]
        values, persisted = self.settings.values, self.settings.persistedValues
        if not values.get("hotkey") or (self._step == 2 and not (values.get("actionHotkeys") or [""])[0]):
            self._message = "请先确认地图键与控件键，再继续。"
        elif any(values.get(k) != persisted.get(k) for k in fields):
            self._pending = "next" if self._step == 1 else "bindings"
            if not self.settings.saveFields(fields):
                self._pending = ""
                self._message = self.settings.message
        elif self._step == 1:
            self._step, self._message = 2, ""
        elif not self._snapshot()["canFinish"]:
            self._message = "请用已确认的控件键显示一次，再隐藏一次；鼠标兜底会单独记录。"
        else:
            self._pending = "finish"
            if persisted.get("onboardingCompleted") is True:
                self._pending = ""
                self.dismiss()
            elif not (self.settings.setValue("onboardingCompleted", True)
                      and self.settings.saveFields(["onboardingCompleted"])):
                self._pending = ""
                self._message = self.settings.message
        self.changed.emit()

    def _saved(self):
        pending, self._pending = self._pending, ""
        if not self._active or self.coordinator.closing:
            return
        if pending == "next":
            self._step, self._message = 2, ""
        elif pending == "bindings":
            self._message = "快捷键已保存，请用新的控件键重新显示、隐藏各一次。"
        elif pending == "finish" and self.settings.persistedValues.get("onboardingCompleted") is True:
            self.dismiss()
        self.changed.emit()
