"""In-app tutorial: shipped chapters/images, progress file and the real QML player."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMetaObject, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PIL import Image
from client_tutorial import ACTION_NAMES, CHAPTER_FILE, PAGES, TUTORIAL_ROOT, TutorialService, load_chapters

HERE = Path(__file__).resolve().parent


class Settings(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.persistedValues = dict(hotkey="G", actionHotkeys=["F9", "F6", "F7", "F8", "", "", "F11", "", "F10"])


class Coordinator(QObject):
    def __init__(self):
        super().__init__()
        self.pages = []

    @Slot(str)
    def navigate(self, page):
        self.pages.append(page)


class TutorialDataTests(unittest.TestCase):
    def test_chapters_images_and_pages_are_complete(self):
        chapters, faq = load_chapters()
        ids = [chapter["id"] for chapter in chapters]
        self.assertEqual(len(ids), len(set(ids)))
        pages = set(re.search(r'property var pages: \[(.*?)\]', (HERE / "Client.qml").read_text("utf-8"))[1]
                    .replace('"', "").replace(" ", "").split(","))
        icons = {path.stem for path in (HERE / "icons").glob("*.png")}
        images = []
        for chapter in chapters:
            self.assertTrue(chapter["title"] and chapter["summary"] and chapter["steps"], chapter["id"])
            self.assertIn(chapter["icon"], icons)
            self.assertTrue(not chapter["page"] or (chapter["page"] in pages and chapter["page"] in PAGES), chapter["id"])
            for step in chapter["steps"]:
                self.assertTrue(step["title"] and step["text"], step)
                self.assertLessEqual(len(step["tips"]), 3, step["title"])
                self.assertTrue(set(step["keys"]) <= set(ACTION_NAMES), step["title"])
                images.append(step["image"])
        self.assertEqual(len(images), len(set(images)))
        self.assertEqual(set(images), {path.name for path in TUTORIAL_ROOT.glob("*.png")})
        total = 0
        for name in images:
            path = TUTORIAL_ROOT / name
            total += path.stat().st_size
            self.assertLessEqual(path.stat().st_size, 120_000, name)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")  # Core Qt format; no optional image plugin.
                self.assertLessEqual(image.width, 1120, name)
        self.assertLessEqual(total, 2_000_000)
        self.assertTrue(all(row["q"] and row["a"] for row in faq))

    def test_spec_packages_chapters_and_images(self):
        spec = (HERE.parents[1] / "tools/qt-client.spec").read_text(encoding="utf-8")
        self.assertIn('qt_root / "tutorial-chapters.json"', spec)
        self.assertIn('(qt_root / "tutorial").glob("*.png")', spec)


class TutorialServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-tutorial-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "tutorial-state.json"
        self.settings = Settings()

    def service(self):
        service = TutorialService(self.path, settings=self.settings)
        self.addCleanup(service.shutdown)
        return service

    def settle(self, service):
        deadline = time.monotonic() + 5
        while service.pendingWrites and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(service.pendingWrites)

    def test_missing_or_corrupt_state_starts_fresh(self):
        self.assertEqual((self.service().done, self.service().inviteVisible), ([], True))
        self.path.parent.mkdir(parents=True)
        for text in ("{broken", "[]", json.dumps(dict(done="start", invited="yes"))):
            self.path.write_text(text, encoding="utf-8")
            service = self.service()
            self.assertEqual((service.done, service.inviteVisible), ([], True), text)

    def test_progress_persists_off_thread_and_drops_unknown_chapters(self):
        service = self.service()
        service.markDone("round")
        service.markDone("round")
        service.markDone("not-a-chapter")
        service.dismissInvite()  # Coalesced behind the running write.
        self.settle(service)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, dict(version=1, done=["round"], invited=True))
        data["done"].append("removed-chapter")
        self.path.write_text(json.dumps(data), encoding="utf-8")
        again = self.service()
        self.assertEqual((again.done, again.inviteVisible), (["round"], False))
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_unwritable_location_keeps_memory_state(self):
        blocker = Path(self.temp.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        self.path = blocker / "tutorial-state.json"
        service = self.service()
        service.markDone("start")
        self.settle(service)
        self.assertEqual(service.done, ["start"])

    def test_bindings_follow_saved_settings_and_images_are_local(self):
        service = self.service()
        changes = []
        service.changed.connect(lambda: changes.append(1))
        step = next(chapter for chapter in service.chapters if chapter["id"] == "round")["steps"][0]
        self.assertEqual(step["bindings"], "本局开始 / 结束 F10")
        self.assertEqual(QUrl(step["imageUrl"]).toLocalFile(), str(TUTORIAL_ROOT / "round-1.png"))
        self.settings.changed.emit()
        self.assertEqual(changes, [])  # Unchanged bindings do not refresh QML.
        self.settings.persistedValues["actionHotkeys"][8] = "Ctrl+F2"
        self.settings.changed.emit()
        self.assertEqual(changes, [1])
        step = next(chapter for chapter in service.chapters if chapter["id"] == "round")["steps"][0]
        self.assertEqual(step["bindings"], "本局开始 / 结束 Ctrl+F2")

    def test_open_requests_only_known_chapters(self):
        service = self.service()
        requested = []
        service.openRequested.connect(requested.append)
        service.open("custom")
        service.open("missing")
        service.open()
        self.assertEqual(requested, ["custom", "", ""])


class TutorialQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-tutorial-qml-")
        self.addCleanup(self.temp.cleanup)
        self.service = TutorialService(Path(self.temp.name) / "tutorial-state.json", settings=Settings())
        self.addCleanup(self.service.shutdown)
        self.coordinator = Coordinator()
        self.engine = QQmlEngine()
        self.engine.rootContext().setContextProperty("tutorialService", self.service)
        self.engine.rootContext().setContextProperty("clientCoordinator", self.coordinator)
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(str(row) for row in rows))
        self.component = component = QQmlComponent(self.engine)
        component.setData(b"""import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { width: 960; height: 720; visible: false
ClientTutorial { objectName: "tutorialPopup" } }
""", QUrl.fromLocalFile(str(HERE / "__tutorial_test__.qml")))
        self.assertFalse(component.isError(), str(component.errors()))
        self.window = component.create()
        self.assertIsNotNone(self.window, str(component.errors()))
        self.addCleanup(self.window.deleteLater)
        self.popup = self.window.findChild(QObject, "tutorialPopup")

    def click(self, name):
        button = self.window.findChild(QObject, name)
        self.assertIsNotNone(button, name)
        self.assertTrue(QMetaObject.invokeMethod(button, "clicked"))
        self.app.processEvents()

    def test_player_steps_finish_and_navigation(self):
        self.service.open("round")
        self.app.processEvents()
        self.assertTrue(self.popup.property("opened") or self.popup.property("visible"))
        self.assertEqual(self.popup.property("chapterId"), "round")
        image = self.window.findChild(QObject, "tutorialImage")
        self.assertTrue(image.property("source").toString().endswith("tutorial/round-1.png"))
        self.assertIn("F10", self.window.findChild(QObject, "tutorialBindings").property("text"))
        self.assertFalse(self.window.findChild(QObject, "tutorialPrev").property("enabled"))
        for _ in range(4):
            self.click("tutorialNext")
        self.assertEqual(self.popup.property("step"), 4)
        self.assertEqual(self.window.findChild(QObject, "tutorialNext").property("text"), "看完了")
        self.assertTrue(self.window.findChild(QObject, "tutorialPage").property("visible"))
        self.click("tutorialNext")
        self.assertEqual(self.popup.property("chapterId"), "")
        self.assertIn("round", self.service.done)
        self.service.open("route")
        self.app.processEvents()
        self.popup.setProperty("step", 2)
        self.app.processEvents()
        self.click("tutorialPage")
        self.assertEqual(self.coordinator.pages, ["gallery"])
        self.assertEqual(image.property("source").toString(), "")  # Closing releases the step image.
        self.assertEqual(self.warnings, [])

    def test_back_returns_to_index_then_closes(self):
        self.service.open("custom")
        self.app.processEvents()
        self.click("tutorialBack")
        self.assertEqual(self.popup.property("chapterId"), "")
        self.click("tutorialBack")
        self.app.processEvents()
        self.assertFalse(self.popup.property("opened"))
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
