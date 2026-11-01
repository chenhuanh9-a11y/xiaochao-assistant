"""Real QML window evidence and isolated upgrade/editor fallback regression."""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_client_custom_gallery as fixture
from test_client_custom_gallery import APP, until
from client_custom_gallery import CustomGalleryService
from client_paths import RuntimePaths
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt, QCoreApplication, QEvent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QFontDatabase, QFont
if Path("C:/Windows/Fonts/msyh.ttc").is_file():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
APP.setFont(QFont("Microsoft YaHei UI", 10))
from PySide6.QtTest import QTest


class EditorUpgradeTests(fixture.CustomGalleryTests):
    def production_service(self, outer=None, legacy=None):
        self.service.shutdown()
        internal = self.sandbox / '_internal'
        internal.mkdir(exist_ok=True)
        shutil.copytree(self.library.root, internal / 'qt-editor-base')
        for name, value in [('assets/custom-maps', outer), ('_internal/assets/custom-maps', legacy)]:
            if value is None:
                continue
            destination = self.sandbox / name
            destination.mkdir(parents=True)
            if value == 'valid':
                shutil.copytree(self.library.root, destination, dirs_exist_ok=True)
            elif value == 'invalid':
                (destination / 'manifest.json').write_text('{broken', encoding='utf8')
        self.addCleanup(patch.stopall)
        patch('client_paths.RESOURCE_ROOT', internal).start()
        paths = RuntimePaths(self.sandbox, True)
        patch('client_custom_gallery.runtime_paths', return_value=paths).start()
        self.service = CustomGalleryService(self.sandbox, default_path=self.default)
        until(lambda: not self.service.busy and bool(self.service.snapshot['maps']))
        return paths

    def test_empty_outer_and_corrupt_legacy_choose_complete_program_baseline(self):
        self.production_service('empty', 'invalid')
        self.assertEqual(self.service.library.root.name, 'qt-editor-base')
        self.assertTrue(self.service.snapshot['imageUrl'])
        self.assertEqual((self.sandbox / '_internal/assets/custom-maps/manifest.json').read_text(), '{broken')

    def test_valid_outer_wins_and_old_gallery_routes_survive(self):
        self.production_service('valid', 'invalid')
        self.assertEqual(self.service.library.root, self.sandbox / 'assets/custom-maps')
        self.call('createGallery', 'preserved')
        self.call('addPoint', 20., 20.)
        before = self.service.path.read_bytes()
        self.call('refresh')
        self.assertEqual(before, self.service.path.read_bytes())
        self.assertEqual(len(self.service.snapshot['points']), 1)

    def test_fresh_and_valid_legacy_selection(self):
        self.production_service(None, 'valid')
        self.assertEqual(self.service.library.root, self.sandbox / '_internal/assets/custom-maps')

    def test_conflicting_gallery_keeps_maps_browsable_and_blocks_all_writes(self):
        self.service.path.write_text('{"schema":"old-unsupported","galleries":[]}', encoding='utf8')
        before = self.service.path.read_bytes()
        state = self.call('refresh')
        self.assertTrue(state['maps'])
        self.assertTrue(state['imageUrl'])
        self.assertTrue(state['readonly'])
        self.assertTrue(state['dataError'])
        self.call('createGallery', 'must not write')
        self.call('addPoint', 20., 20.)
        self.assertEqual(before, self.service.path.read_bytes())
        self.service.path.unlink()
        self.call('refresh')
        self.assertFalse(self.service.snapshot['dataError'])

    def test_synthetic_map_drawing_surface_has_usable_width_in_real_window(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('customGalleryService', self.service)
        engine.loadData(b'import QtQuick\nimport QtQuick.Window\nWindow { width: 1000; height: 720; visible: true; ClientCustomGallery { anchors.fill: parent; anchors.margins: 24 } }', QUrl.fromLocalFile(str(Path(__file__).with_name('EditorEvidence.qml'))))
        window = engine.rootObjects()[0]
        APP.processEvents()
        image = window.findChild(QQuickItem, 'customMapImage')
        self.assertGreater(image.width(), 400)
        self.assertGreater(image.height(), 250)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_missing_legacy_and_outer_use_fresh_program_baseline(self):
        self.production_service()
        self.assertEqual(self.service.library.root.name, 'qt-editor-base')

    def test_hash_invalid_outer_is_preserved_and_falls_back_to_legacy(self):
        self.production_service('valid', 'valid')
        manifest = json.loads((self.service.library.root / 'manifest.json').read_text('utf8'))
        image = self.service.library.root / manifest['maps'][0]['floors']['1']['image']
        image.write_bytes(b'corrupted-old-image')
        self.call('refresh')
        self.assertEqual(self.service.library.root, self.sandbox / '_internal/assets/custom-maps')
        self.assertEqual(image.read_bytes(), b'corrupted-old-image')

    @unittest.skipUnless(os.environ.get('QT_EDITOR_REAL_LIBRARY'), 'authorized real editor library not supplied')
    def test_real_editor_window_map_floor_choice_and_mouse_route(self):
        self.service.shutdown()
        internal = self.sandbox / '_internal/assets/custom-maps'
        shutil.copytree(Path(os.environ['QT_EDITOR_REAL_LIBRARY']), internal)
        self.service = CustomGalleryService(self.sandbox, default_path=self.default)
        until(lambda: bool(self.service.snapshot['maps']) and not self.service.busy, timeout=10)
        self.assertEqual(len(self.service.snapshot['maps']), 26)
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(r) for r in rows))
        engine.rootContext().setContextProperty('customGalleryService', self.service)
        engine.loadData(b'import QtQuick\nimport QtQuick.Window\nWindow { width: 1180; height: 820; visible: true; color: "#edf4ff"; ClientCustomGallery { anchors.fill: parent; anchors.margins: 24 } }', QUrl.fromLocalFile(str(Path(__file__).with_name('EditorEvidence.qml'))))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        def close_window():
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
        self.addCleanup(close_window)
        image = window.findChild(QQuickItem, 'customMapImage')
        choice = window.findChild(QQuickItem, 'customMapChoice')
        floors = window.findChild(QQuickItem, 'customFloorChoice')
        surface = window.findChild(QQuickItem, 'customMapDrawingSurface')
        def click(item):
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
            APP.processEvents()
        click(choice)
        QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Return)
        until(lambda: not self.service.busy and self.service.snapshot['selectedMap'] != 'nightmare-03')
        selected = self.service.snapshot['selectedMap']
        click(floors)
        QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Return)
        until(lambda: not self.service.busy)
        self.assertEqual(self.service.snapshot['selectedFloor'], '2')
        self.call('selectFloor', '1')
        self.call('createGallery', '隔离窗口路线验收')
        until(lambda: image.property('progress') == 1 and image.property('paintedWidth') > 0, timeout=10)
        graph = self.service.library.graph(selected)
        asset = self.service.library.floor(selected, '1')
        picked = []
        for xy in graph.floors['1'].points:
            point = graph.pick('1', *xy)
            if not point or any(all(point.get(k)==p.get(k) for k in ('x','y','room','kind')) for p in picked):
                continue
            if picked and abs(point['x']-picked[0]['x']) + abs(point['y']-picked[0]['y']) < 400:
                continue
            u,v,z = asset.to_image @ [point['x'], point['y'], 1]
            u,v = u/z,v/z
            if not (10 < u < asset.image_size[0]-10 and 10 < v < asset.image_size[1]-10):
                continue
            scale = image.property('paintedWidth') / asset.image_size[0]
            x = (surface.width()-image.property('paintedWidth'))/2 + u*scale
            y = (surface.height()-image.property('paintedHeight'))/2 + v*scale
            pos = surface.mapToScene(QPointF(x,y))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos.toPoint())
            until(lambda: not self.service.busy)
            if len(self.service.snapshot['points']) > len(picked):
                picked.append(point)
            if len(picked) == 2:
                break
        self.assertGreater(image.width(), 500)
        self.assertGreater(surface.height(), 300)
        self.assertEqual(len(self.service.snapshot['points']), 2)
        self.assertTrue(self.service.snapshot['lines'])
        for _ in range(15):
            APP.processEvents()
            QTest.qWait(10)
        out = Path(os.environ.get('QT_EDITOR_EVIDENCE', str(self.folder)))
        out.mkdir(parents=True, exist_ok=True)
        self.assertTrue(window.grabWindow().save(str(out / 'editor-map-route.png')))
        (out / 'editor-evidence.json').write_text(json.dumps(dict(map=selected, floor=self.service.snapshot['selectedFloor'], maps=len(self.service.snapshot['maps']), floors=len(self.service.snapshot['floors']), points=len(picked), lines=len(self.service.snapshot['lines']), warnings=warnings), ensure_ascii=False, indent=2), encoding='utf8')
        self.assertFalse(warnings)


if __name__ == '__main__':
    unittest.main()
