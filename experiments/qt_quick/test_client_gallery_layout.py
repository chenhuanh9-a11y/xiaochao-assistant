"""One bounded gallery layout/action flow; synthetic rows and own Qt windows."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, Property, QPoint, QPointF, Qt, QUrl, Signal, Slot, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from backend import ResourceRows, GalleryProfiles

APP = QGuiApplication.instance() or QGuiApplication([])


class FakeBackend(QObject):
    changed = Signal()
    viewChanging = Signal(str)
    viewChanged = Signal(str)

    def __init__(self, image):
        super().__init__()
        self.model = ResourceRows()
        self.image = image
        self.rows = [dict(key=f"nightmare/author-a/map-{n}/variant-1/{1+n%2}",
                          title=f"测试地图 {n}", mapId=f"map-{n}", floor=str(1+n%2),
                          variant="路线一", imageUrl=image, available=True) for n in range(40)]
        self.model.reconcile(deepcopy(self.rows))
        self._season = "s0"
        self._mode, self._profile, self._floor, self._query = "nightmare", "author-a", "", ""
        self.selected = {}
        self.calls = []
        self.gallery_profiles = GalleryProfiles()
        self.gallery_profiles.reconcile_profiles(self.profiles)
        self.changed.connect(lambda: self.gallery_profiles.reconcile_profiles(self.profiles))

    season = Property(str, lambda self: self._season, notify=changed)
    mode = Property(str, lambda self: self._mode, notify=changed)
    profiles = Property("QVariantList", lambda self: getattr(self, "_profiles_override", None) or [dict(id="author-a", name="作者甲", runtimeSelectable=True, available=40, availableMaps=40, mapCount=40, previewUrl=self.image),
        dict(id="author-b", name="作者乙", runtimeSelectable=True, available=12, availableMaps=12, mapCount=40, previewUrl=self.image)], notify=changed)
    galleryProfileModel = Property(QObject, lambda self: self.gallery_profiles, constant=True)

    @Slot(bool)
    def setActive(self, active):
        self._active = bool(active)
    profileId = Property(str, lambda self: self._profile, notify=changed)
    floor = Property(str, lambda self: self._floor, notify=changed)
    floorOptions = Property("QVariantList", lambda self: [dict(name=name, value=value) for name, value in
        (("全部楼层", ""), ("1 楼", "1"), ("2 楼", "2"), ("B1 地下室", "B1"))], notify=changed)
    count = Property(int, lambda self: len(self.model.rows), notify=changed)
    mapCount = Property(int, lambda self: len({row['mapId'] for row in self.model.rows if row['available']}), notify=changed)
    selectedKey = Property(str, lambda self: self.selected.get("key", ""), notify=changed)
    selectedRow = Property(int, lambda self: next((i for i,row in enumerate(self.model.rows) if row['key']==self.selectedKey), -1), notify=changed)
    selectedTitle = Property(str, lambda self: self.selected.get("title", ""), notify=changed)
    selectedFloor = Property(str, lambda self: self.selected.get("floor", ""), notify=changed)
    selectedImage = Property(str, lambda self: self.selected.get("imageUrl", ""), notify=changed)
    _busy = False
    busy = Property(bool, lambda self: self._busy, notify=changed)
    hasError = Property(bool, lambda self: False, notify=changed)
    message = Property(str, lambda self: "隔离测试资源", notify=changed)
    viewKey = Property(str, lambda self: f"{self._mode}/{self._profile}/{self._floor}/{self._query}", notify=changed)

    @Slot()
    def selectFirstLayer(self):
        if not self.selected and self.model.rows:
            self.select(self.model.rows[0]["key"])

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

    @Slot()
    def showOverlay(self):
        self.calls.append(("preview", None))

    def change(self, field, value):
        self.viewChanging.emit(self.viewKey)
        setattr(self, field, value)
        self.changed.emit()
        self.viewChanged.emit(self.viewKey)

    @Slot(str, str)
    def setContext(self, season, mode):
        self.change("_season", season)
        self.setMode(mode)

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

    @Slot()
    def clearGallerySearch(self):
        self._query = ""

    @Slot(str)
    def search(self, value):
        self.calls.append(("query", value))
        self.change("_query", value)


class FakeState(QObject):
    changed = Signal()
    seasonOptions = Property("QVariantList", lambda self: [{"id":"s0","label":"S0"},{"id":"s1","label":"S1"}], constant=True)
    _season = "s0"
    currentSeason = Property(str, lambda self: self._season, notify=changed)
    currentMode = Property(str, lambda self: "nightmare", notify=changed)
    _profile = "shared-structure"
    currentProfile = Property(str, lambda self: self._profile, notify=changed)
    _busy = False
    busy = Property(bool, lambda self: self._busy or getattr(self, "_resource_busy", False), notify=changed)
    operationStage = Property(str, lambda self: getattr(self, "_resource_stage", ""), notify=changed)
    message = Property(str, lambda self: "正在核对图包", notify=changed)


class FakeCoordinator(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    @Slot(str)
    def navigate(self, value): self.calls.append(("navigate", value))

    @Slot(str, str, str)
    def useGalleryAuthor(self, season, mode, value): self.calls.append(("use", value))

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
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
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
        self.assertTrue(item.isVisible() and item.isEnabled(), name)
        # Layout changes from list/detail or resize can be queued one frame.
        for _ in range(100):
            APP.processEvents()
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            if 0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height():
                break
            time.sleep(.01)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        scroll = self.item('galleryDetailScroll')
        ancestor = item.parentItem()
        while ancestor is not None and ancestor is not scroll:
            ancestor = ancestor.parentItem()
        if ancestor is scroll:
            # The compact detail is one native scroll area. Reach controls via
            # actual wheel input, rather than clicking through its clip rect.
            for _ in range(8):
                top = item.mapToScene(QPointF(0, 0))
                local = scroll.mapFromScene(top)
                if 0 <= local.y() and local.y() + item.height() <= scroll.height():
                    break
                center = scroll.mapToScene(QPointF(scroll.width()/2, scroll.height()/2))
                QTest.wheelEvent(self.window, center, QPoint(0, 120 if local.y() < 0 else -120))
                QTest.qWait(25)
                deadline = time.monotonic() + 2
                while scroll.property('moving') and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertFalse(scroll.property('moving'), 'scroll must settle before pointer activation')
                point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            local = scroll.mapFromScene(item.mapToScene(QPointF(0, 0)))
            self.assertTrue(0 <= local.y() and local.y() + item.height() <= scroll.height() + 1, name)
        self.assertTrue(0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height(), (name, point))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        APP.processEvents()
        QTest.qWait(10)

    def test_window_hidden_minimized_and_restored_updates_content_activity(self):
        self.assertTrue(self.backend._active)
        self.window.hide()
        QTest.qWait(25)
        self.assertTrue(self.page.isVisible(), 'Item visibility alone does not track its window')
        self.assertFalse(self.backend._active)
        self.window.showMinimized()
        QTest.qWait(25)
        self.assertTrue(self.page.isVisible())
        self.assertFalse(self.backend._active)
        self.window.showNormal()
        QTest.qWait(25)
        self.assertTrue(self.backend._active)

    def test_compact_keyboard_focus_reveals_detail_actions(self):
        self.resize(514, 386)
        self.click('galleryViewAuthor_author-a')
        action = self.item('galleryShowReference')
        scroll = self.item('galleryDetailScroll')
        self.window.requestActivate()
        QTest.qWait(25)
        action.forceActiveFocus(Qt.TabFocusReason)
        QTest.qWait(25)
        self.assertTrue(action.hasActiveFocus())
        top = action.mapToScene(QPointF(0, 0))
        local = scroll.mapFromScene(top)
        self.assertGreaterEqual(local.y(), 0)
        self.assertLessEqual(local.y() + action.height(), scroll.height() + 1)

    def test_author_capability_alias_and_busy_states_fail_closed(self):
        row = dict(id="author-a", name="测试作者", available=1, availableMaps=1, mapCount=1)
        self.backend._profiles_override = [row]
        self.backend.changed.emit()
        APP.processEvents()
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        row["runtimeSelectable"] = False
        self.backend.changed.emit()
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        row.update(runtimeSelectable=True, runtimeProfileId="shared-structure")
        self.backend.changed.emit()
        self.assertEqual(self.item("galleryUseAuthor").property("text"), "正在使用结构底图")
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        self.state._profile = "another"
        self.state.changed.emit()
        self.assertEqual(self.item("galleryUseAuthor").property("text"), "使用结构底图")
        self.assertTrue(self.item("galleryUseAuthor").isEnabled())
        for owner in (self.backend, self.state):
            owner._busy = True
            owner.changed.emit()
            self.assertFalse(self.item("galleryUseAuthor").isEnabled())
            owner._busy = False
            owner.changed.emit()
        row["available"] = 0
        self.backend.changed.emit()
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())

    def test_s1_author_uses_explicit_action_and_context_guard(self):
        self.backend.setContext("s1", "nightmare")
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        self.state._season = "s1"
        self.state.changed.emit()
        APP.processEvents()
        self.assertTrue(self.item("galleryUseAuthor").isEnabled())
        self.click("galleryViewAuthor_author-b")
        self.click("galleryShowReference")
        self.assertEqual(self.coordinator.calls, [])
        self.click("galleryUseAuthor")
        self.assertEqual(self.coordinator.calls, [("use", "author-b")])
        self.backend.setMode("difficult")
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())

    def test_import_progress_stays_visible_across_browse_context_and_narrow_layout(self):
        self.state._resource_busy, self.state._resource_stage = True, "install"
        self.state.changed.emit()
        self.resize(514, 440)
        card = self.item("packageProgressCard")
        self.assertTrue(card.isVisible())
        self.assertEqual(card.property("phase"), "解压并导入")
        self.assertGreater(card.height(), 60)
        self.assertGreater(self.item("packageProgressBar").width(), 100)
        self.backend.setContext("s1", "difficult")
        QTest.qWait(20)
        self.assertTrue(card.isVisible())
        self.assertEqual(self.item("packageProgressCard"), card)
        self.page.setVisible(False)
        self.state._resource_stage = "refresh-after-write"
        self.state.changed.emit()
        self.page.setVisible(True)
        QTest.qWait(20)
        self.assertEqual(card.property("phase"), "核对安装结果")
        self.state._resource_busy = False
        self.state.changed.emit()
        QTest.qWait(20)
        self.assertFalse(card.isVisible())
        self.assertEqual(self.warnings, [])

    def test_keyed_refresh_and_narrow_list_detail_keep_selection_and_actions(self):
        self.assertFalse(self.page.findChild(QObject, "clientGallerySearch"))
        self.assertEqual(self.item("galleryMapCount").property("text"), "仅浏览 · 40 份图")
        self.assertGreater(self.item("galleryManagePackages").mapToScene(QPointF()).y(), self.item("galleryRefresh").mapToScene(QPointF()).y())
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        self.click("galleryViewAuthor_author-a")
        self.click("galleryLayerPicker")
        self.click("galleryLayerOption_2")
        key = self.backend.rows[2]["key"]
        self.assertEqual(self.backend.selectedKey, key)
        preview = self.item("galleryLargePreview")
        picker = self.item("galleryLayerPicker")
        self.assertLess(preview.mapToScene(QPointF()).y(), picker.mapToScene(QPointF()).y())
        floor_picker = self.item("galleryFloorPicker")
        floor_picker.forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.window, Qt.Key_Down)
        APP.processEvents()
        self.assertEqual(self.backend.calls[-1], ("floor", "1"))
        before_mutations = self.backend.model.mutations
        self.click("galleryRefresh")
        self.assertEqual(self.backend.selectedKey, key)
        self.assertEqual(self.backend.model.mutations, before_mutations)
        self.resize(514, 440)
        self.assertTrue(self.page.property("compact"))
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.assertFalse(self.item("galleryPreviewPane").isVisible())
        self.click("galleryViewAuthor_author-a")
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        self.assertFalse(self.item("galleryListPane").isVisible())
        self.click("galleryUseAuthor")
        self.assertEqual(self.coordinator.calls[-1], ("use", "author-a"))
        self.click("galleryShowReference")
        self.assertEqual(self.backend.calls[-1], ("preview", None))
        self.click("galleryManagePackages")
        self.assertEqual(self.coordinator.calls[-1], ("navigate", "resources"))
        self.click("galleryBackToList")
        self.assertEqual(self.backend.selectedKey, key)
        self.assertTrue(self.item("galleryListPane").isVisible())
        self.resize(1000, 650)
        self.assertEqual(self.backend.selectedKey, key)
        self.assertTrue(self.item("galleryPreviewPane").isVisible())
        context_picker = self.item("galleryContextSeason")
        self.assertEqual(context_picker.property("count"), 2)
        self.click("galleryContextDifficult")
        APP.processEvents()
        self.assertEqual(self.backend.calls[-1], ("mode", "difficult"))
        self.assertFalse(self.item("galleryUseAuthor").isEnabled())
        self.assertFalse(self.warnings, "\n".join(self.warnings))

    def test_wide_preview_reserves_actual_height_for_coverage_caption(self):
        def coverage(item):
            if str(item.property("text") or "").startswith("本地覆盖 "):
                return item
            for child in item.childItems():
                found = coverage(child)
                if found is not None:
                    return found
        for width, height in ((1000, 650), (1200, 900)):
            with self.subTest(size=(width, height)):
                self.resize(width, height)
                scroll = self.item("galleryDetailScroll")
                scroll.setProperty("contentY", 0)
                caption = coverage(scroll)
                self.assertIsNotNone(caption)
                bottom = caption.mapToItem(scroll, QPointF()).y() + caption.height()
                self.assertLessEqual(bottom, scroll.height() + .1)
        self.assertFalse(self.warnings, "\n".join(self.warnings))


if __name__ == "__main__":
    unittest.main()
