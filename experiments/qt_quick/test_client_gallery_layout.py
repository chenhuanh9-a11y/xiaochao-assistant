"""One bounded gallery layout/action flow; synthetic rows and own Qt windows."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, Property, QPoint, QPointF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from backend import ResourceRows

APP = QGuiApplication.instance() or QGuiApplication([])


class FakeBackend(QObject):
    changed = Signal()
    viewChanging = Signal(str)
    viewChanged = Signal(str)

    def __init__(self, image):
        super().__init__()
        self.model = ResourceRows()
        self.rows = [dict(key=f"nightmare/author-a/map-{n}/variant-1/{1+n%2}",
                          title=f"测试地图 {n}", mapId=f"map-{n}", floor=str(1+n%2),
                          variant="路线一", imageUrl=image, available=True) for n in range(40)]
        self.model.reconcile(deepcopy(self.rows))
        self._mode, self._profile, self._floor, self._query = "nightmare", "author-a", "", ""
        self.selected = {}
        self.calls = []

    mode = Property(str, lambda self: self._mode, notify=changed)
    profiles = Property("QVariantList", lambda self: [dict(id="author-a", name="作者甲", available=40),
        dict(id="author-b", name="作者乙", available=12)], notify=changed)
    profileId = Property(str, lambda self: self._profile, notify=changed)
    floor = Property(str, lambda self: self._floor, notify=changed)
    count = Property(int, lambda self: len(self.model.rows), notify=changed)
    selectedKey = Property(str, lambda self: self.selected.get("key", ""), notify=changed)
    selectedTitle = Property(str, lambda self: self.selected.get("title", ""), notify=changed)
    selectedFloor = Property(str, lambda self: self.selected.get("floor", ""), notify=changed)
    selectedImage = Property(str, lambda self: self.selected.get("imageUrl", ""), notify=changed)
    busy = Property(bool, lambda self: False, notify=changed)
    hasError = Property(bool, lambda self: False, notify=changed)
    message = Property(str, lambda self: "隔离测试资源", notify=changed)
    viewKey = Property(str, lambda self: f"{self._mode}/{self._profile}/{self._floor}/{self._query}", notify=changed)

    @Slot(str)
    def select(self, key):
        self.calls.append(("select", key))
        self.selected = next(row for row in self.model.rows if row["key"] == key)
        self.changed.emit()

    @Slot()
    def refresh(self):
        self.calls.append(("refresh", None))
        self.model.reconcile(deepcopy(self.rows))
        self.changed.emit()

    def change(self, field, value):
        self.viewChanging.emit(self.viewKey)
        setattr(self, field, value)
        self.changed.emit()
        self.viewChanged.emit(self.viewKey)

    @Slot(str)
    def setMode(self, value):
        self.calls.append(("mode", value))
        self.change("_mode", value)

    @Slot(str)
    def setProfile(self, value):
        self.calls.append(("profile", value))
        self.change("_profile", value)

    @Slot(str)
    def setFloor(self, value):
        self.calls.append(("floor", value))
        self.change("_floor", value)

    @Slot(str)
    def search(self, value):
        self.calls.append(("query", value))
        self.change("_query", value)


class FakeState(QObject):
    changed = Signal()
    currentMode = Property(str, lambda self: "nightmare", notify=changed)
    currentProfile = Property(str, lambda self: "shared-structure", notify=changed)
    busy = Property(bool, lambda self: False, notify=changed)


class FakeCoordinator(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    @Slot(str)
    def navigate(self, value): self.calls.append(("navigate", value))

    @Slot(str)
    def setRouteProfile(self, value): self.calls.append(("use", value))

    @Slot()
    def showReference(self): self.calls.append(("reference", None))


class GalleryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-gallery-layout-")
        image = QImage(120, 100, QImage.Format_RGBA8888)
        image.fill(0xffcedbed)
        path = Path(self.temp.name) / "synthetic.png"
        self.assertTrue(image.save(str(path)))
        self.backend = FakeBackend(QUrl.fromLocalFile(str(path)).toString())
        self.state, self.coordinator = FakeState(), FakeCoordinator()
        self.engine = QQmlEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(row.toString() for row in rows))
        for name, value in dict(backend=self.backend, resourceModel=self.backend.model,
                session=self.state, settingsService=self.state, resourceService=self.state,
                clientCoordinator=self.coordinator).items():
            self.engine.rootContext().setContextProperty(name, value)
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientGallery.qml"))))
        self.assertFalse(self.component.isError(), "\n".join(row.toString() for row in self.component.errors()))
        self.page = self.component.create()
        self.assertIsNotNone(self.page, "\n".join(row.toString() for row in self.component.errors()))
        self.window = QQuickWindow()
        self.page.setParentItem(self.window.contentItem())
        self.resize(1000, 650)
        self.window.show()
        QTest.qWait(25)

    def tearDown(self):
        self.window.hide()
        self.page.setParentItem(None)
        self.page.deleteLater()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def resize(self, width, height):
        self.window.resize(width, height)
        self.page.setWidth(width)
        self.page.setHeight(height)
        APP.processEvents()
        QTest.qWait(15)

    def item(self, name):
        def find(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                result = find(child)
                if result is not None:
                    return result
            return None
        result = find(self.window.contentItem())
        self.assertIsNotNone(result, name)
        return result

    def click(self, name):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        APP.processEvents()
        QTest.qWait(10)

    def test_keyed_refresh_and_narrow_list_detail_keep_selection_and_actions(self):
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        self.click("galleryLayer_1")
        key = self.backend.rows[1]["key"]
        self.assertEqual(self.backend.selectedKey, key)
        self.click("galleryLayerPicker")
        self.click("galleryLayerOption_2")
        key = self.backend.rows[2]["key"]
        self.assertEqual(self.backend.selectedKey, key)
        floor_picker = self.item("galleryFloorPicker")
        floor_picker.forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.window, Qt.Key_Down)
        APP.processEvents()
        self.assertEqual(self.backend.calls[-1], ("floor", "1"))
        gallery = self.item("clientResourceGrid")
        gallery.setProperty("contentY", 220)
        APP.processEvents()
        before_scroll, before_mutations = gallery.property("contentY"), self.backend.model.mutations
        self.click("galleryRefresh")
        self.assertEqual(self.backend.selectedKey, key)
        self.assertEqual(gallery.property("contentY"), before_scroll)
        self.assertEqual(self.backend.model.mutations, before_mutations)
        self.resize(514, 335)
        self.assertTrue(self.page.property("compact"))
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.assertFalse(self.item("galleryPreviewPane").isVisible())
        gallery.setProperty("contentY", 0)
        APP.processEvents()
        self.click("galleryLayer_0")
        key = self.backend.rows[0]["key"]
        self.assertEqual(self.backend.selectedKey, key)
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        self.assertFalse(self.item("galleryListPane").isVisible())
        footer = self.item("gallerySelectionFooter")
        self.assertLessEqual(footer.mapToScene(QPointF(0, footer.height())).y(), self.page.height()+1)
        self.click("galleryUseAuthor")
        self.assertEqual(self.coordinator.calls[-1], ("use", "author-a"))
        self.click("galleryShowReference")
        self.assertEqual(self.coordinator.calls[-1], ("reference", None))
        self.click("galleryManagePackages")
        self.assertEqual(self.coordinator.calls[-1], ("navigate", "resources"))
        self.click("galleryBackToList")
        self.assertEqual(self.backend.selectedKey, key)
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.resize(1000, 650)
        self.assertEqual(self.backend.selectedKey, key)
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        self.click("galleryDifficultMode")
        self.assertEqual(self.backend.calls[-1], ("mode", "difficult"))
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        self.assertNotIn(("use", "difficult"), self.coordinator.calls)
        self.assertFalse(self.warnings, "\n".join(self.warnings))


if __name__ == "__main__":
    unittest.main()
