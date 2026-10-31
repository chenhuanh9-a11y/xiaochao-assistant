"""Controls binding contract with synthetic services, no native input/game."""
import os
from pathlib import Path
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, QMetaObject
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow


class Settings(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.data = dict(interfaceOpacity=1.0, sheetVisibilityEnabled=True, referenceSheetVisible=True,
            actionHotkeys=["F9", "F6", "F7", "F8", "F4", "F12", "F11", "F10", "F10"],
            gameControlLayout=dict(order=["display", "mode", "manual_map", "recognition", "round"],
                enabled=["display", "manual_map", "round"], display=dict(referencePosition=True)))
        self.saved = []
    values = Property("QVariantMap", lambda s: s.data, notify=changed)
    busy = Property(bool, lambda s: False, constant=True)
    @Slot(str, "QVariant")
    def setValue(self, key, value):
        self.data[key] = value; self.changed.emit()
    @Slot("QVariantList")
    def saveFields(self, keys):
        self.saved.append(list(keys))


class Session(QObject):
    changed = Signal()
    manual_ready = False
    snapshot = Property("QVariantMap", lambda s: dict(userVisible=None, roundActive=False,
        availableFloors=[], commands={name: dict(available=s.manual_ready if name == "reselect_map" else False) for name in
            ("start_round", "reselect_map", "cycle_floor")}), notify=changed)
    busy = Property(bool, lambda s: False, constant=True)
    currentMode = Property(str, lambda s: "nightmare", constant=True)


class Coordinator(QObject):
    changed = Signal()
    referenceEditing = Property(bool, lambda s: False, constant=True)
    boundWindow = Property(str, lambda s: "", constant=True)
    def __init__(self, settings):
        super().__init__(); self.settings = settings; self.manual_requests = 0
    def toggle(self, key):
        self.settings.setValue(key, not self.settings.data[key]); self.settings.saveFields([key])
    @Slot()
    def requestManualMapSelection(self): self.manual_requests += 1
    @Slot()
    def toggleAlignedVisibility(self): self.toggle("sheetVisibilityEnabled")
    @Slot()
    def toggleReferenceVisibility(self): self.toggle("referenceSheetVisible")


class ControlActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if Path("C:/Windows/Fonts/msyh.ttc").is_file():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")

    def test_unknown_recognition_does_not_replace_persistent_toggle_semantics(self):
        settings, session = Settings(), Session()
        coordinator = Coordinator(settings)
        engine = QQmlApplicationEngine()
        warnings = []; engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        for name, value in dict(settingsService=settings, session=session,
                                clientCoordinator=coordinator, windows=QObject()).items():
            engine.rootContext().setContextProperty(name, value)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientControls.qml"))))
        self.assertEqual(len(engine.rootObjects()), 1, warnings)
        window = engine.rootObjects()[0]
        def items(item):
            yield item
            for child in item.childItems(): yield from items(child)
        def named(name):
            return next(i for i in items(window.contentItem()) if i.objectName() == name)
        aligned = named("controlAlignedVisibility")
        self.assertEqual(aligned.property("text"), "隐藏贴合")
        self.assertTrue(aligned.property("selected"))
        self.assertTrue(QMetaObject.invokeMethod(aligned, "clicked"))
        self.app.processEvents()
        self.assertEqual(aligned.property("text"), "显示贴合")
        self.assertEqual(settings.saved[-1], ["sheetVisibilityEnabled"])
        reference = named("controlReferenceVisibility")
        self.assertTrue(QMetaObject.invokeMethod(reference, "clicked"))
        self.app.processEvents()
        self.assertEqual(reference.property("text"), "显示定点")
        self.assertEqual(settings.saved[-1], ["referenceSheetVisible"])
        self.assertFalse(named("controlReselectMap").property("enabled"))
        self.assertFalse(named("controlRoundAction").property("enabled"))
        session.manual_ready = True; session.changed.emit(); self.app.processEvents()
        self.assertTrue(named("controlReselectMap").property("enabled"))
        self.assertEqual(named("controlReselectMap").property("text"), "选择地图")
        self.assertTrue(QMetaObject.invokeMethod(named("controlReselectMap"), "clicked"))
        self.assertEqual(coordinator.manual_requests, 1)
        self.assertFalse(named("controlRoundAction").property("enabled"))
        self.assertEqual(window.property("activeModules").toVariant(), ["display", "manual_map", "round"])
        self.assertEqual(warnings, [])
        window.deleteLater(); self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
