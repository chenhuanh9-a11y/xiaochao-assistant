"""Synthetic signals only: no audio output, native hooks, game or user settings."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QObject, Signal
from client_ui_feedback import UiFeedback


class Fake(QObject):
    changed = Signal()
    saved = Signal()
    windowChanged = Signal(str)
    controlVisibilityChanged = Signal(bool, str)


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.settings, self.windows, self.session, self.coordinator = [Fake() for _ in range(4)]
        self.settings.persistedValues = dict(uiSoundEnabled=True)
        self.windows.snapshot = dict(controls=dict(collapsed=True))
        self.session.snapshot = {}
        self.coordinator.closing = self.coordinator._closed = False
        self.time, self.played = [10.], []
        self.feedback = UiFeedback(settings=self.settings, windows=self.windows,
            session=self.session, coordinator=self.coordinator, player=self.played.append,
            clock=lambda: self.time[0])

    def test_actual_edges_throttle_and_shutdown(self):
        self.assertEqual(self.played, [])
        self.windows.windowChanged.emit("controls")
        self.assertEqual(self.played, [])
        self.windows.snapshot["controls"]["collapsed"] = False
        self.windows.windowChanged.emit("controls")
        self.coordinator.controlVisibilityChanged.emit(False, "mouse")
        self.assertEqual(self.played, ["SystemAsterisk"])
        self.time[0] += .2
        self.coordinator.controlVisibilityChanged.emit(False, "keyboard")
        self.assertEqual(self.played, ["SystemAsterisk", "SystemDefault"])
        self.coordinator.closing = True
        self.time[0] += .2
        self.feedback.play("confirm")
        self.assertEqual(len(self.played), 2)

    def test_sound_enable_edge_and_probe_suppression(self):
        self.settings.persistedValues["uiSoundEnabled"] = False
        self.settings.saved.emit()
        self.settings.persistedValues["uiSoundEnabled"] = True
        self.settings.saved.emit()
        self.assertEqual(self.played, ["SystemAsterisk"])
        self.time[0] += .2
        self.settings.saved.emit()
        self.assertEqual(len(self.played), 1)
        self.feedback._allowed = False
        self.feedback.play("confirm")
        self.assertEqual(len(self.played), 1)

    def test_confirm_requires_new_successful_live_completion(self):
        self.session.snapshot = dict(lastReceipt=dict(action="confirm_route", success=True))
        self.session.changed.emit()  # An existing/replayed receipt is silent.
        self.assertEqual(self.played, [])
        for success in (False, True):
            self.session.snapshot = dict(busy=True, pendingAction="confirm_route")
            self.session.changed.emit()
            self.session.snapshot = dict(lastReceipt=dict(action="confirm_route", success=success))
            self.session.changed.emit()
        self.session.changed.emit()
        self.assertEqual(self.played, ["SystemAsterisk"])


if __name__ == "__main__":
    unittest.main()
