"""Default QtClient structure/author choices reach the actual image consumer.

The resource fixture supplies an isolated S1D base and user author PNG. Only
the starting game context is seeded through SettingsService; no route profile
is seeded. Both display choices use the real Gallery QML button.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import time
import traceback
import unittest
import numpy as np
from unittest.mock import Mock, patch
from uuid import uuid4

from PIL import Image
from io import BytesIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QPoint, QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

import client
from backend import Backend
from client_coordinator import ClientCoordinator
from client_paths import WORK_ROOT
from client_settings import SettingsService
from client_imported_gallery_images import ImportedGalleryImages
import test_client_imported_author_selection as imported_author_fixture
import test_imported_author_display as imported_display_fixture


PROFILE_ID = "author-s1-ui-fixture"
AUTHOR_PIXEL = (201, 49, 71, 255)


def _find_visual_item(root, name):
    if root is None:
        return None
    if root.objectName() == name:
        return root
    # QQuickItem's visual children can live outside QObject ownership trees
    # (notably Repeater delegates), so recurse through childItems directly.
    for child in root.childItems():
        result = _find_visual_item(child, name)
        if result is not None:
            return result
    return None


class ImportedGalleryDefaultUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if os.environ.get("XINGTU_TEST_RESOURCE_UI_EVIDENCE") and os.name == "nt":
            for name in ("msyh.ttc", "msyhbd.ttc"):
                QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))

    def test_visual_tree_lookup_finds_and_clicks_repeater_delegate(self):
        from PySide6.QtQml import QQmlComponent
        from PySide6.QtQuick import QQuickWindow

        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick\nimport QtQuick.Controls.Basic\nWindow {
            id: root
            width: 240; height: 100; visible: true; property int clickCount: 0
            Row { Repeater { model: [{key:"home"},{key:"gallery"}]
                Button { objectName: "nav_" + modelData.key; width: 100; height: 40
                    onClicked: root.clickCount++ }
            } }
        }''', QUrl())
        self.assertTrue(component.isReady(),
            "\n".join(error.toString() for error in component.errors()))
        window = component.create()
        self.assertIsInstance(window, QQuickWindow,
            "\n".join(error.toString() for error in component.errors()))
        window.show()
        QTest.qWait(25)
        target = _find_visual_item(window.contentItem(), "nav_gallery")
        self.assertIsNotNone(target, "\n".join(error.toString() for error in component.errors()))
        point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point, 10)
        QTest.qWait(25)
        self.assertEqual(window.property("clickCount"), 1)
        window.close()
        engine.deleteLater()

    def test_default_client_user_author_choice_reaches_gallery_pixels(self):
        from client_input import NativeShortcutBackend
        from client_resource_startup import repair_managed_base_resources
        from client_route_plaza import RoutePlazaService
        from test_client_input import Native
        from test_client_live_runtime import FixtureCapture

        fixture_type = imported_author_fixture._AuthorSelectionFixture
        fixture_type.setUpClass()
        self.addCleanup(fixture_type.doClassCleanups)
        fixture = fixture_type("runTest")
        self.addCleanup(fixture.doCleanups)
        real_temporary_directory = tempfile.TemporaryDirectory
        with patch.object(imported_display_fixture.tempfile, "TemporaryDirectory",
                          side_effect=lambda *args, **kwargs: real_temporary_directory(
                              *args, **dict(kwargs, dir=kwargs.get("dir", WORK_ROOT)))):
            fixture.setUp()

        root = fixture.root
        (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1\n", encoding="utf-8")
        fixture.seed()
        author_png = BytesIO()
        Image.new("RGBA", (5, 3), AUTHOR_PIXEL).save(author_png, format="PNG")
        view, _, _ = fixture.package(package_id="qt-default-gallery-author",
            profile_id=PROFILE_ID, payloads={"images/route.png": author_png.getvalue()})
        activation = fixture.activate(view)
        self.assertEqual(activation["status"], "catalog-activated")
        self.assertEqual(activation["profileId"], PROFILE_ID)
        self.assertEqual(tuple(fixture.source.floors), ("-1", "-2", "-3", "-4"))

        # Set only the game context needed to expose the S1D author choice.
        # The S1D route preference remains absent until the Gallery button is
        # clicked in the live QML below.
        setup_settings = SettingsService(root)
        self.assertTrue(setup_settings.setValue("gameSeason", "s1"))
        self.assertTrue(setup_settings.setValue("gameMode", "difficult"))
        self.assertTrue(setup_settings.saveFields(["gameSeason", "gameMode"]))
        deadline = time.monotonic() + 5
        while setup_settings.busy and time.monotonic() < deadline:
            self.app.processEvents()
            # This loop waits for a Python QRunnable doing the real durable
            # write. Yield the GIL as well as pumping queued UI completion.
            time.sleep(.002)
        self.assertFalse(setup_settings.busy, setup_settings.message)
        self.assertEqual(setup_settings.persistedValues["contextRouteProfiles"].get("s1:difficult"), None)
        setup_settings.shutdown()

        evidence_root = Path(__file__).resolve().parents[2] / "work" / "startup-performance-20261110" / "imported-gallery-ui"
        evidence_dir = evidence_root / uuid4().hex[:10]
        evidence_dir.mkdir(parents=True, exist_ok=False)
        capture = FixtureCapture()
        capture.is_current = lambda *args, **kwargs: True
        guard = Mock()
        guard.start.return_value = True
        holder = {"phase": "startup", "errors": [], "profileId": PROFILE_ID,
                  "journeyStarted": time.monotonic(), "phaseHistory": [], "captures": {}}

        constructor = client.QtClient

        def construct(*args, **kwargs):
            created = kwargs.get("on_created")

            def retain(partial):
                holder["client"] = partial
                if created is not None:
                    created(partial)

            kwargs["on_created"] = retain
            holder["client"] = constructor(*args, **kwargs)
            return holder["client"]

        def offline_plaza(*args, **kwargs):
            return RoutePlazaService(*args, **kwargs, listing=lambda query: [])

        def no_network(*args, **kwargs):
            raise AssertionError("Imported gallery UI attempted networking")

        poll, watchdog = QTimer(), QTimer()
        poll.setInterval(15)
        watchdog.setSingleShot(True)

        def item(name):
            instance = holder.get("client")
            if instance is None:
                return None
            found = _find_visual_item(instance.main.contentItem(), name)
            if found is not None:
                return found
            return instance.main.findChild(QObject, name)

        def visible_navigation_names(instance):
            names = []
            def collect_names(current):
                if current is None:
                    return
                if current.objectName().startswith("nav_"):
                    names.append(current.objectName())
                for child in current.childItems():
                    collect_names(child)
            collect_names(instance.main.contentItem())
            return sorted(names)

        def reveal_in_clip(target, name):
            if "failurePhase" in holder:
                return False
            instance = holder["client"]
            clips = []
            ancestor = target.parentItem()
            while ancestor is not None:
                if ancestor.property('moving'):
                    return False
                if ancestor.clip():
                    local = ancestor.mapFromScene(target.mapToScene(QPointF(0, 0)))
                    clips.append(dict(name=ancestor.objectName(), left=local.x(), top=local.y(),
                        width=ancestor.width(), height=ancestor.height(), contentY=ancestor.property('contentY')))
                ancestor = ancestor.parentItem()
            origin = target.mapToScene(QPointF(0, 0))
            layout = dict(name=name, phase=holder['phase'], x=origin.x(), y=origin.y(),
                          width=target.width(), height=target.height(), clips=clips)
            holder['lastLayout'] = layout
            previous = holder.setdefault('layoutPositions', {})
            key = (holder['phase'], name)
            stable = previous.get(key) == layout
            previous[key] = layout
            # Compact panes are resized and polished asynchronously. A loaded
            # Image can retain its old width while its new viewport is negative.
            # Wait for two matching observations before evaluating hit geometry.
            if not stable or any(clip['width'] <= 0 or clip['height'] <= 0 for clip in clips):
                return False
            ancestor = target.parentItem()
            while ancestor is not None:
                if ancestor.property('moving'):
                    return False
                if ancestor.clip():
                    local = ancestor.mapFromScene(target.mapToScene(QPointF(0, 0)))
                    self.assertTrue(-1 <= local.x() and local.x() + target.width() <= ancestor.width() + 1,
                        dict(name=name, clip=ancestor.objectName(), left=local.x(),
                             width=target.width(), viewport=ancestor.width()))
                    if local.y() < 0 or local.y() + target.height() > ancestor.height() + 1:
                        holder['lastScroll'] = dict(name=name, phase=holder['phase'], clip=ancestor.objectName(),
                            top=local.y(), height=target.height(), viewport=ancestor.height(),
                            contentY=ancestor.property('contentY'))
                        if ancestor.property('contentY') is not None:
                            counts = holder.setdefault('clipScrollCounts', {})
                            self.assertLess(counts.get(name, 0), 8, holder['lastScroll'])
                            counts[name] = counts.get(name, 0) + 1
                            center = ancestor.mapToScene(QPointF(ancestor.width()/2, ancestor.height()/2))
                            QTest.wheelEvent(instance.main, center, QPoint(0, 120 if local.y() < 0 else -120))
                            QTest.qWait(25)
                        return False
                ancestor = ancestor.parentItem()
            return True

        def click(name):
            if "failurePhase" in holder:
                return False
            instance = holder["client"]
            target = item(name)
            if (target is None or not isinstance(target, QQuickItem) or not target.isVisible() or not target.isEnabled()
                    or target.width() <= 0 or target.height() <= 0):
                holder["lastClick"] = dict(name=name, found=target is not None,
                    type=type(target).__name__ if target is not None else None,
                    visible=target.isVisible() if isinstance(target, QQuickItem) else None,
                    enabled=target.isEnabled() if isinstance(target, QQuickItem) else None,
                    size=(target.width(), target.height()) if isinstance(target, QQuickItem) else None,
                    matchingNames=visible_navigation_names(instance))
                return False
            if not reveal_in_clip(target, name):
                return False
            point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
            if not (0 <= point.x() < instance.main.width() and 0 <= point.y() < instance.main.height()):
                holder['lastClick'] = dict(name=name, point=(point.x(), point.y()),
                    windowSize=(instance.main.width(), instance.main.height()), outsideWindow=True)
                return False
            QTest.mouseClick(instance.main, Qt.LeftButton, Qt.NoModifier, point, 10)
            if 'failurePhase' not in holder:
                holder.setdefault('clickEvidence', []).append(dict(name=name, phase=holder['phase'],
                    center=(point.x(), point.y()), layout=holder['lastLayout']))
            return "failurePhase" not in holder

        def choose_s1(name):
            if "failurePhase" in holder:
                return False
            instance = holder["client"]
            combo = item(name)
            if combo is None or not combo.isVisible() or combo.width() <= 0:
                return False
            point = combo.mapToScene(QPointF(combo.width() / 2, combo.height() / 2)).toPoint()
            QTest.mouseClick(instance.main, Qt.LeftButton, Qt.NoModifier, point, 10)
            if "failurePhase" in holder:
                return False
            # The season selector's stable order ends with S1. Keyboard events
            # activate the actual ComboBox; no QML setter or signal is invoked.
            QTest.keyClick(instance.main, Qt.Key_End, Qt.NoModifier, 10)
            if "failurePhase" in holder:
                return False
            QTest.keyClick(instance.main, Qt.Key_Return, Qt.NoModifier, 10)
            return "failurePhase" not in holder

        def loaded(instance, page, name):
            loader = item(name)
            if instance.main.property("page") != page or loader is None:
                return False
            expression = QQmlExpression(QQmlEngine.contextForObject(loader), loader,
                                        "status === Loader.Ready && item !== null")
            value = expression.evaluate()
            if expression.hasError():
                raise AssertionError(expression.error().toString())
            return value[0] if isinstance(value, tuple) else value

        def qml(instance, obj, expression):
            evaluate = QQmlExpression(QQmlEngine.contextForObject(obj), obj, expression)
            value = evaluate.evaluate()
            if evaluate.hasError():
                raise AssertionError(evaluate.error().toString())
            return value[0] if isinstance(value, tuple) else value

        def save_failure_log(error=None):
            instance = holder.get("client")
            detail = {
                "phase": holder.get("failurePhase", holder.get("phase")),
                "error": str(error or holder.get("collectorError") or "watchdog-timeout"),
                "settings": dict(holder.get("settings", {})),
                "session": dict(holder.get("session", {})),
                "backend": dict(holder.get("backend", {})),
                "lastClick": holder.get("lastClick"),
                "lastScroll": holder.get('lastScroll'),
                "clipScrollCounts": dict(holder.get('clipScrollCounts', {})),
                "lastLayout": holder.get('lastLayout'),
                "clickEvidence": list(holder.get('clickEvidence', [])),
                "captures": dict(holder['captures']),
                "phaseHistory": list(holder["phaseHistory"]),
                "qmlWarnings": list(getattr(instance, "warnings", ())) if instance else [],
                "window": dict(page=instance.main.property('page'), width=instance.main.width(),
                               height=instance.main.height()) if instance else None,
            }
            if error is not None:
                detail["traceback"] = traceback.format_exc()
            (evidence_dir / "failure.log").write_text(json.dumps(detail, ensure_ascii=False,
                indent=2, default=str), encoding="utf-8")

        def set_snapshot_details(instance):
            holder["settings"] = {
                "values": dict(instance.settings.values),
                "persistedValues": dict(instance.settings.persistedValues),
                "busy": instance.settings.busy,
                "message": instance.settings.message,
            }
            holder["session"] = {
                "snapshot": dict(instance.session.snapshot),
                "current": (instance.session.currentSeason, instance.session.currentMode,
                            instance.session.currentProfile),
                "applied": (instance.session.appliedSeason, instance.session.appliedMode,
                            instance.session.appliedProfile),
                "initialized": instance._initialized,
                "preparationTimerActive": instance._initialize_timer.isActive(),
            }
            holder["backend"] = {
                "season": instance.backend.season,
                "mode": instance.backend.mode,
                "busy": instance.backend.busy,
                "profileId": instance.backend.profileId,
                "profiles": list(instance.backend.profiles),
                "rows": list(instance.backend.model.rows),
                "floorOptions": list(instance.backend.floorOptions),
            }

        def collect():
            instance = holder.get("client")
            if instance is None or not getattr(instance, "_initialized", False):
                return
            phase = holder["phase"]
            if phase == "startup":
                if not instance.main.isVisible() or instance.settings.busy:
                    return
                state = instance.session.snapshot
                report = state.get("savedFrameCapabilities", {}).get("difficult", {})
                # _initialized means inspect_engine_resources was dispatched,
                # not that the real imported provider finished preparation.
                if (instance.session.busy or state.get("contextPending")
                        or report.get("season") != "s1" or report.get("mode") != "difficult"
                        or report.get("prepared") is not True):
                    return
                self.assertTrue(instance.session.engineConnected, state)
                self.assertTrue(report.get("dataReady"), report)
                self.assertTrue(report.get("rankReady"), report)
                if "resourceReadyAtSeconds" not in holder:
                    holder["resourceReadyAtSeconds"] = time.monotonic() - holder["journeyStarted"]
                    # Keep the original interaction/exit budget after actual
                    # Session readiness; cold native preparation has its own cap.
                    watchdog.start(60000)
                self.assertIsInstance(instance.backend, Backend)
                self.assertIsInstance(instance.route_choices, Backend)
                self.assertIsInstance(instance.settings, SettingsService)
                self.assertIsInstance(instance.coordinator, ClientCoordinator)
                self.assertIs(instance.backend.resource_resolver, instance.resource_resolver)
                self.assertIs(instance.route_choices.resource_resolver, instance.resource_resolver)
                self.assertIs(instance.coordinator.settings, instance.settings)
                self.assertIsInstance(instance.gallery_images, ImportedGalleryImages)
                self.assertIsNone(instance.settings.persistedValues["contextRouteProfiles"].get("s1:difficult"))
                if click("nav_gallery"):
                    holder["phase"] = "gallery-page"
                return
            if phase == "gallery-page":
                if not loaded(instance, "gallery", "lazyGalleryPage"):
                    return
                if choose_s1("galleryContextSeason"):
                    holder["phase"] = "gallery-season"
                return
            if phase == "gallery-season":
                if instance.backend.season != "s1":
                    return
                if click("galleryContextDifficult"):
                    holder["phase"] = "gallery-mode"
                return
            if phase == "gallery-mode":
                if (instance.backend.season, instance.backend.mode) != ("s1", "difficult"):
                    return
                if instance.backend.busy or not instance.backend.profiles:
                    return
                if not any(row["id"] == PROFILE_ID and row["available"] > 0
                           and row["runtimeSelectable"] is True for row in instance.backend.profiles):
                    return
                if not instance.backend.model.rows or instance.backend.selectedImage == "":
                    return
                if not holder.get('structureDone'):
                    if click('galleryViewAuthor_s1-native-structure'):
                        holder['phase'] = 'structure-image'
                    return
                if not click("galleryViewAuthor_" + PROFILE_ID):
                    return
                holder["phase"] = "gallery-image"
                return
            if phase == 'structure-image':
                image = item('galleryLargePreview')
                if (image is None or not image.isVisible() or instance.backend.busy
                        or not qml(instance, image, 'status === Image.Ready')):
                    return
                self.assertEqual(instance.backend.profileId, 's1-native-structure')
                structure = next(row for row in instance.backend.profiles if row['id'] == 's1-native-structure')
                self.assertFalse(structure['userProvided'])
                self.assertEqual(structure['runtimeProfileId'], 'shared-structure')
                self.assertEqual(item('galleryUseAuthor').property('text'), '使用结构底图')
                context = instance.backend.context
                selected = next(row for row in instance.backend.model.rows if row['key'] == instance.backend.selectedKey)
                self.assertIsNone(context.catalog)
                profile = next(row for row in context.profiles if row.profile_id == 's1-native-structure')
                selected_map = next(row for row in profile.maps if row.id == selected['mapId'])
                path = next(variant.layers[selected['floor']] for variant in selected_map.variants
                            if selected['floor'] in variant.layers)
                source_pixels = context.assets.pixels(context.assets.key(path))
                source = instance.backend.selectedImage.split('image://importedgallery/', 1)[1]
                pixels = instance.gallery_images.requestImage(source, QSize(), QSize())
                self.assertFalse(pixels.isNull())
                self.assertEqual((pixels.width(), pixels.height()), (source_pixels.shape[1], source_pixels.shape[0]))
                holder['structureSourceSize'] = (source_pixels.shape[1], source_pixels.shape[0])
                ys, xs = np.nonzero(source_pixels[..., 3])
                self.assertGreater(len(ys), 0)
                np.testing.assert_array_equal(source_pixels[ys[len(ys)//2], xs[len(xs)//2]],
                                              (218, 228, 238, 255))
                self.assertEqual(pixels.pixelColor(int(xs[len(xs)//2]), int(ys[len(ys)//2])).getRgb(),
                                 (238, 228, 218, 255))
                holder['structureCatalogBefore'] = (fixture.resources/'catalog.json').read_bytes()
                if click('galleryUseAuthor'):
                    holder['phase'] = 'structure-applied'
                return
            if phase == 'structure-applied':
                if (instance.settings.busy or instance.session.busy
                        or instance.settings.persistedValues['contextRouteProfiles'].get('s1:difficult') != 'shared-structure'
                        or (instance.session.appliedSeason, instance.session.appliedMode,
                            instance.session.appliedProfile) != ('s1', 'difficult', 'shared-structure')):
                    return
                self.assertEqual((fixture.resources/'catalog.json').read_bytes(), holder['structureCatalogBefore'])
                self.assertEqual(instance.session.snapshot['mapId'], '')
                self.assertFalse(instance.session.snapshot['identityLocked'])
                self.assertEqual(item('galleryUseAuthor').property('text'), '正在使用结构底图')
                instance.main.resize(650, 470)
                holder['captureDeadline'] = time.monotonic() + .18
                holder['phase'] = 'structure-small'
                return
            if phase == 'structure-small':
                if time.monotonic() < holder['captureDeadline']:
                    return
                image = item('galleryLargePreview')
                if image is None or not image.isVisible():
                    if not click('galleryViewAuthor_s1-native-structure'):
                        return
                holder['phase'] = 'structure-small-image'
                return
            if phase == 'structure-small-image':
                image = item('galleryLargePreview')
                if (image is None or not image.isVisible() or not qml(instance, image, 'status === Image.Ready')
                        or not reveal_in_clip(image, 'galleryLargePreview')):
                    return
                name = 'structure-650x470.png'
                holder['captures'][name] = self._capture_image_evidence(instance, image, evidence_dir/name,
                    expected_window_size=(650, 470), expected_source_size=holder['structureSourceSize'],
                    expected_pixel=(238, 228, 218, 255))
                if 'failurePhase' in holder:
                    return
                instance.main.resize(1000, 720)
                holder['captureDeadline'] = time.monotonic() + .18
                holder['phase'] = 'structure-large'
                return
            if phase == 'structure-large':
                if time.monotonic() < holder['captureDeadline']:
                    return
                image = item('galleryLargePreview')
                if (image is None or not image.isVisible() or not qml(instance, image, 'status === Image.Ready')
                        or not reveal_in_clip(image, 'galleryLargePreview')):
                    return
                name = 'structure-1000x720.png'
                holder['captures'][name] = self._capture_image_evidence(instance, image, evidence_dir/name,
                    expected_window_size=(1000, 720), expected_source_size=holder['structureSourceSize'],
                    expected_pixel=(238, 228, 218, 255))
                if 'failurePhase' in holder:
                    return
                instance.main.resize(650, 470)
                holder['structureDone'] = True
                holder['captureDeadline'] = time.monotonic() + .18
                holder['phase'] = 'structure-author-list'
                return
            if phase == 'structure-author-list':
                if time.monotonic() < holder['captureDeadline']:
                    return
                image = item('galleryLargePreview')
                if image is not None and image.isVisible():
                    if not click('galleryBackToList'):
                        return
                holder['phase'] = 'gallery-mode'
                return
            if phase == "gallery-image":
                image = item("galleryLargePreview")
                if (image is None or not image.isVisible() or instance.backend.busy
                        or instance.backend.selectedImage == ""
                        or not qml(instance, image, "status === Image.Ready")):
                    return
                context = instance.backend.context
                self.assertIsNotNone(context)
                self.assertEqual(context.mode, "difficult")
                self.assertEqual(context.season, "s1")
                self.assertEqual(tuple(context.pack.floors), tuple(fixture.source.floors))
                self.assertEqual(tuple(row.id for row in context.pack.maps), tuple(fixture.source.ids))
                floors = {row["value"]: row["name"] for row in instance.backend.floorOptions}
                self.assertEqual({floor: floors[floor] for floor in ("-4", "-3", "-2", "-1")},
                                 {"-4": "B4", "-3": "B3", "-2": "B2", "-1": "B1"})
                self.assertEqual(instance.backend.selectedFloor, "-4")
                self.assertTrue(instance.backend.selectedImage.startswith("image://importedgallery/"))
                from imported_resource_reader import ComponentResourceReader
                self.assertIsNone(context.catalog, 'browse declarations cannot grant runtime identity')
                declaration = ComponentResourceReader(view).json(view.component['entrypoint'])
                self.assertEqual(declaration['profileId'], PROFILE_ID)
                self.assertFalse(declaration['identityAuthority'])
                self.assertFalse(declaration['structureValidationAuthority'])
                self.assertFalse(declaration['crossFloorProjectionAllowed'])
                self.assertTrue(declaration['currentFrameGeometryFitRequired'])
                profile = next(row for row in context.profiles if row.profile_id == PROFILE_ID)
                self.assertEqual(profile.package_id, view.component_ref.package_id)
                path = profile.first_preview[2]
                self.assertEqual(context.assets.key(path), (PROFILE_ID, fixture.source.ids[0], '-4'))
                np.testing.assert_array_equal(context.assets.pixels(context.assets.key(path))[0, 0],
                                              (71, 49, 201, 255))
                self.assertFalse(instance.session.snapshot.get("liveAuthorization", False))
                holder["galleryModel"] = instance.backend.galleryProfileModel
                snapshot = instance.resource_resolver.snapshot()
                holder["resourceIdentityBefore"] = (
                    snapshot.contexts["s1:difficult"]["recognitionBase"],
                    snapshot.contexts["s1:difficult"]["controls"],
                    tuple(snapshot.displays))
                holder['catalogBefore'] = (fixture.resources/'catalog.json').read_bytes()
                holder["galleryRowsBefore"] = tuple((row["mapId"], row["floor"], row["key"])
                    for row in instance.backend.model.rows)
                holder["imageSource"] = instance.backend.selectedImage
                holder["phase"] = "use-author-click"
                return
            if phase == "use-author-click":
                if not click("galleryUseAuthor"):
                    return
                holder["phase"] = "use-author-applied"
                return
            if phase == "use-author-applied":
                persisted = instance.settings.persistedValues
                applied = (instance.session.appliedSeason, instance.session.appliedMode,
                           instance.session.appliedProfile)
                if (instance.settings.busy or instance.session.busy
                        or persisted["contextRouteProfiles"].get("s1:difficult") != PROFILE_ID
                        or applied != ("s1", "difficult", PROFILE_ID)):
                    return
                self.assertIs(instance.backend.galleryProfileModel, holder["galleryModel"])
                self.assertEqual(instance.backend.profileId, PROFILE_ID)
                snapshot = instance.resource_resolver.snapshot()
                self.assertEqual((snapshot.contexts["s1:difficult"]["recognitionBase"],
                                  snapshot.contexts["s1:difficult"]["controls"],
                                  tuple(snapshot.displays)),
                                 holder["resourceIdentityBefore"])
                self.assertEqual((fixture.resources/'catalog.json').read_bytes(), holder['catalogBefore'])
                self.assertEqual(tuple((row["mapId"], row["floor"], row["key"])
                    for row in instance.backend.model.rows), holder["galleryRowsBefore"])
                self.assertEqual(instance.session.snapshot.get("liveAuthorization", False), False)
                capabilities = instance.session.snapshot['savedFrameCapabilities']['difficult']
                self.assertTrue(capabilities['prepared'])
                self.assertTrue(capabilities['dataReady'])
                self.assertTrue(capabilities['rankReady'])
                self.assertFalse(capabilities['liveReady'])
                floor_capability = capabilities['floorObservation']
                self.assertEqual(floor_capability['evidence'], 'private-test-four-floor-contract')
                self.assertEqual(floor_capability['sourceReplayFloors'], [-1, -2, -3, -4])
                self.assertFalse(floor_capability['liveValidated'])
                self.assertTrue(capabilities['candidateObservation']['candidateReady'])
                self.assertFalse(capabilities['candidateObservation']['liveValidated'])
                image = item('galleryLargePreview')
                if image is None or not image.isVisible():
                    if not click("galleryViewAuthor_" + PROFILE_ID):
                        return
                instance.main.resize(650, 470)
                holder["captureDeadline"] = time.monotonic() + 0.18
                holder["phase"] = "small-layout"
                return
            if phase == "small-layout":
                if instance.main.width() != 650 or instance.main.height() != 470:
                    return
                if time.monotonic() < holder["captureDeadline"]:
                    return
                # Wide mode shows both columns without opening compact detail.
                # Enter detail through the real card if resizing shows the list.
                image = item('galleryLargePreview')
                if image is None or not image.isVisible():
                    if not click('galleryViewAuthor_' + PROFILE_ID):
                        return
                holder["phase"] = "small-image"
                return
            if phase == "small-image":
                image = item("galleryLargePreview")
                if (image is None or not image.isVisible() or not qml(instance, image, "status === Image.Ready")
                        or not reveal_in_clip(image, 'galleryLargePreview')):
                    return
                name = 'gallery-650x470.png'
                holder['captures'][name] = self._capture_image_evidence(instance, image, evidence_dir/name,
                    expected_window_size=(650, 470))
                if "failurePhase" in holder:
                    return
                instance.main.resize(1000, 720)
                holder["captureDeadline"] = time.monotonic() + 0.18
                holder["phase"] = "large-layout"
                return
            if phase == "large-layout":
                if instance.main.width() != 1000 or instance.main.height() != 720:
                    return
                if time.monotonic() < holder["captureDeadline"]:
                    return
                image = item("galleryLargePreview")
                if (image is None or not image.isVisible() or not qml(instance, image, "status === Image.Ready")
                        or not reveal_in_clip(image, 'galleryLargePreview')):
                    return
                name = 'gallery-1000x720.png'
                holder['captures'][name] = self._capture_image_evidence(instance, image, evidence_dir/name,
                    expected_window_size=(1000, 720))
                if "failurePhase" in holder:
                    return
                holder["galleryFinishedAtSeconds"] = time.monotonic() - holder["journeyStarted"]
                holder["phase"] = "done"
                poll.stop()
                instance.coordinator.requestClose()

        def collect_result():
            if holder.get('collecting') or "failurePhase" in holder:
                return
            holder['collecting'] = True
            try:
                phase = holder["phase"]
                if not holder["phaseHistory"] or holder["phaseHistory"][-1]["phase"] != phase:
                    instance = holder.get("client")
                    state = instance.session.snapshot if instance is not None and hasattr(instance, "session") else {}
                    report = state.get("savedFrameCapabilities", {}).get("difficult", {})
                    holder["phaseHistory"].append(dict(phase=phase,
                        elapsedSeconds=round(time.monotonic() - holder["journeyStarted"], 3),
                        pendingAction=state.get("pendingAction"),
                        capabilities={key: report.get(key) for key in
                            ("season", "mode", "prepared", "dataReady", "rankReady", "reason")}))
                collect()
            except Exception as error:
                holder["failurePhase"] = holder["phase"]
                poll.stop()
                watchdog.stop()
                holder["collectorError"] = str(error)
                holder["traceback"] = traceback.format_exc()
                instance = holder.get("client")
                if instance is not None:
                    set_snapshot_details(instance)
                save_failure_log(error)
                self.app.exit(89)
            finally:
                holder['collecting'] = False

        def timed_out():
            holder["failurePhase"] = holder["phase"]
            # shutdown_client drains queued Qt events. Stop the driver before
            # that drain so it cannot click an enabled-again author button after
            # Session shutdown or mutate the saved failure phase/preferences.
            poll.stop()
            instance = holder.get("client")
            if instance is not None:
                set_snapshot_details(instance)
            save_failure_log()
            self.app.exit(88)

        poll.timeout.connect(collect_result)
        watchdog.timeout.connect(timed_out)
        with ExitStack() as stack:
            for name in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.sendto",
                         "socket.create_connection", "socket.getaddrinfo"):
                stack.enter_context(patch(name, side_effect=no_network))
            stack.enter_context(patch.object(client, "PRODUCTION", True))
            stack.enter_context(patch.object(client, "owned_sandbox", side_effect=lambda path: Path(path)))
            stack.enter_context(patch.object(client, "QGuiApplication", return_value=self.app))
            stack.enter_context(patch.object(client, "QtClient", side_effect=construct))
            stack.enter_context(patch.object(__import__("sys"), "argv",
                ["client", "--sandbox", str(root), "--probe-output", str(root / "unused-probe.json")]))
            stack.enter_context(patch("client_single_instance.SingleInstanceGuard", return_value=guard))
            stack.enter_context(patch("client_input.NativeShortcutBackend",
                side_effect=lambda: NativeShortcutBackend(Native())))
            stack.enter_context(patch("client_capture.ClientCaptureProvider", return_value=capture))
            stack.enter_context(patch("client_coordinator.another_assistant_running", return_value=False))
            stack.enter_context(patch("client_route_plaza.RoutePlazaService", side_effect=offline_plaza))
            stack.enter_context(patch("probe_client.run_client_probe"))
            stack.enter_context(patch("client_resource_startup_recovery.offer_startup_recovery", return_value=None))
            repair = stack.enter_context(patch("client_resource_startup.repair_managed_base_resources",
                                               wraps=repair_managed_base_resources))
            try:
                poll.start()
                # Match the real imported-author/resource UI fixture's 90s
                # preparation cap (test_client_imported_author_ui.wait).
                # The Gallery interaction budget remains 60s after readiness.
                watchdog.start(90000)
                result = client.main()
                instance = holder.get("client")
                # main() drains and closes SessionService before returning.
                # Keep the collector/watchdog snapshot from the actual failure;
                # a closed snapshot erases pending work and can roll back an
                # author context that had not reached its worker yet.
                if result != 0 and "session" not in holder:
                    set_snapshot_details(instance) if instance is not None else None
                self.assertEqual(result, 0, {
                    "phase": holder.get("failurePhase", holder["phase"]), "collectorError": holder.get("collectorError"),
                    "settings": holder.get("settings"), "session": holder.get("session"),
                    "backend": holder.get("backend"),
                    "phaseHistory": holder["phaseHistory"], "evidenceDir": str(evidence_dir),
                    "qmlWarnings": getattr(instance, "warnings", None),
                })
            finally:
                poll.stop()
                watchdog.stop()
                if holder.get("client") is not None:
                    holder["client"].shutdown()

        self.assertEqual(holder["phase"], "done")
        self.assertTrue(instance._shutdown_complete)
        self.assertEqual(holder["imageSource"].split("/", 3)[0], "image:")
        self.assertEqual(capture.count, 0)
        self.assertEqual(capture.enable_count, 0)
        guard.close.assert_called_once()
        repair.assert_not_called()
        self.assertTrue((evidence_dir / "gallery-650x470.png").is_file())
        self.assertTrue((evidence_dir / "gallery-1000x720.png").is_file())
        self.assertTrue(holder['structureDone'])
        self.assertTrue((evidence_dir / 'structure-650x470.png').is_file())
        self.assertTrue((evidence_dir / 'structure-1000x720.png').is_file())
        self.assertFalse((instance.paths.memory_root('difficult', 's1') / 'samples').exists())
        (evidence_dir / "journey.json").write_text(json.dumps(dict(
            resourceReadyAtSeconds=holder["resourceReadyAtSeconds"],
            galleryDurationSeconds=holder["galleryFinishedAtSeconds"] - holder["resourceReadyAtSeconds"],
            clipScrollCounts=holder.get('clipScrollCounts', {}), lastScroll=holder.get('lastScroll'),
            clickEvidence=holder.get('clickEvidence', []),
            captures=holder['captures'],
            phaseHistory=holder["phaseHistory"]), ensure_ascii=False, indent=2), encoding="utf-8")

    def _capture_image_evidence(self, instance, image, path, *, expected_window_size,
                                expected_source_size=(5, 3), expected_pixel=AUTHOR_PIXEL):
        from PySide6.QtQuick import QQuickWindow
        self.assertIsInstance(instance.main, QQuickWindow)
        source = image.property("source")
        source_text = source.toString() if isinstance(source, QUrl) else str(source)
        self.assertTrue(source_text.startswith("image://importedgallery/"), source_text)
        url = QUrl(source_text)
        identifier = url.path().lstrip("/")
        returned = instance.gallery_images.requestImage(identifier, QSize(), QSize())
        self.assertFalse(returned.isNull(), identifier)
        self.assertEqual((returned.width(), returned.height()), expected_source_size)
        self.assertEqual(tuple(returned.pixelColor(0, 0).getRgb()), expected_pixel)
        self.assertEqual((instance.main.width(), instance.main.height()), expected_window_size)
        # Image.Ready describes loading; allow the own window's scene graph to
        # paint the newly opened compact pane before checking its output.
        instance.main.requestUpdate()
        QTest.qWait(80)
        shot = instance.main.grabWindow()
        self.assertFalse(shot.isNull())
        self.assertEqual((shot.width(), shot.height()),
                         tuple(round(value * shot.devicePixelRatio()) for value in expected_window_size))
        self.assertTrue(shot.save(str(path)), str(path))
        geometry, current = [], image
        while current is not None:
            point = current.mapToScene(QPointF(0, 0))
            geometry.append(dict(name=current.objectName(), type=type(current).__name__,
                x=point.x(), y=point.y(), width=current.width(), height=current.height(), clip=current.clip()))
            current = current.parentItem()
        origin = image.mapToScene(QPointF(image.width() / 2, image.height() / 2)).toPoint()
        sample_x = round(origin.x() * shot.devicePixelRatio())
        sample_y = round(origin.y() * shot.devicePixelRatio())
        self.assertTrue(0 <= sample_x < shot.width() and 0 <= sample_y < shot.height())
        self.assertEqual(tuple(shot.pixelColor(sample_x, sample_y).getRgb()), expected_pixel)
        evidence = dict(source=source_text,
            sourceDimensions=dict(width=returned.width(), height=returned.height()),
            windowDimensions=dict(width=instance.main.width(), height=instance.main.height()),
            screenshotDimensions=dict(width=shot.width(), height=shot.height()),
            devicePixelRatio=shot.devicePixelRatio(), samplePoint=(sample_x, sample_y),
            samplePixel=tuple(shot.pixelColor(sample_x, sample_y).getRgb()), geometry=geometry)
        path.with_suffix('.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        return evidence


if __name__ == "__main__":
    unittest.main()
