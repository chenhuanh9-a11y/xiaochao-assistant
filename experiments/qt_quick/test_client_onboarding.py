"""Real settings writer with synthetic controls; no native input or capture."""
import os
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from client_settings import ROOT, SettingsService
from client_onboarding import OnboardingService


class Fake(QObject):
    changed = Signal()
    controlVisibilityChanged = Signal(bool, str)


class OnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qt-onboarding-", dir=ROOT / "work")
        self.path = Path(self.tmp.name)
        (self.path / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        self.settings = SettingsService(self.path)
        self.windows, self.coordinator = Fake(), Fake()
        self.windows.snapshot = dict(controls=dict(visible=False))
        self.coordinator.closing = self.coordinator._closed = False
        self.coordinator.toggleControls = lambda: self.toggle("mouse")
        self.service = OnboardingService(settings=self.settings, windows=self.windows, coordinator=self.coordinator)

    def tearDown(self):
        self.settings.shutdown()
        self.app.processEvents()
        self.tmp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 5
        while self.settings.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(self.settings.busy)

    def toggle(self, source):
        row = self.windows.snapshot["controls"]
        row["visible"] = not row["visible"]
        self.coordinator.controlVisibilityChanged.emit(row["visible"], source)

    def test_skip_replay_and_real_trial_completion(self):
        self.assertTrue(self.service.active)
        self.assertFalse((self.path / "ui-settings.json").exists())
        self.service.dismiss()
        self.assertFalse(self.service.active)
        self.assertFalse(self.settings.persistedValues["onboardingCompleted"])
        self.service.open(); self.service.advance()
        self.assertEqual(self.service.step, 2)
        self.service.advance()
        self.assertTrue(self.service.active)
        self.service.tryControls(True)
        self.service.tryControls(True)  # No transition cannot earn a trial.
        self.assertFalse(self.service.trial["hidden"])
        self.service.tryControls(False)
        self.assertTrue(self.service.trial["mouseUsed"])
        self.assertTrue(self.service.trial["canFinish"])
        self.service.advance(); self.wait()
        self.assertFalse(self.service.active)
        self.assertTrue(json.loads((self.path / "ui-settings.json").read_text())["onboardingCompleted"])
        self.service.open()
        self.assertTrue(self.service.active)

    def test_binding_save_resets_trial_and_requires_new_actual_edges(self):
        self.service.advance()
        self.toggle("keyboard"); self.toggle("keyboard")
        self.assertTrue(self.service.trial["keyboardReady"])
        keys = self.settings.values["actionHotkeys"]
        keys[0] = "F12"
        self.settings.setValue("actionHotkeys", keys)
        self.service.advance(); self.wait()
        self.assertFalse(self.service.trial["canFinish"])
        self.assertEqual(self.service.trial["keyText"], "F12")
        self.assertTrue(self.service.active)

    def test_save_failure_cannot_complete_on_later_unrelated_save(self):
        self.service.advance(); self.toggle("keyboard"); self.toggle("keyboard")
        with patch.object(self.settings, "_write", side_effect=OSError("fixture full disk")):
            self.service.advance(); self.wait()
        self.assertTrue(self.service.active)
        self.assertFalse(self.settings.persistedValues["onboardingCompleted"])
        self.settings.setValue("sheetOpacity", .7)
        self.settings.saveFields(["sheetOpacity"]); self.wait()
        self.assertTrue(self.service.active)
        self.assertFalse(self.settings.persistedValues["onboardingCompleted"])

    def test_qml_load_and_small_window_layout(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("settingsService", self.settings)
        engine.rootContext().setContextProperty("onboardingService", self.service)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        source = b'import QtQuick; import QtQuick.Controls.Basic; ApplicationWindow { width: 650; height: 470; visible: true; ClientOnboarding {} }'
        engine.loadData(source, QUrl.fromLocalFile(str(Path(__file__).parent / "OnboardingFixture.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        self.app.processEvents()
        window = engine.rootObjects()[0]
        popup = window.findChild(QObject, "clientOnboarding")
        self.assertTrue(popup.property("visible"))
        self.assertLessEqual(popup.property("height"), 438)
        self.assertEqual(warnings, [])
        window.close()
        engine.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
