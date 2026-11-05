"""Actual Home/Session and aligned QML consumers; synthetic IO, not game QA."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from dataclasses import replace
from pathlib import Path
import threading
import time
import unittest
import numpy as np
from PySide6.QtCore import QObject, Property, QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from client_coordinator import LiveImages
from client_session import SessionService
from client_windows import WindowController
from s1_structure_selection import StructureCandidateScanner
import test_client_recognition as qml
from test_client_coordinator import CoordinatorTests
from test_s1_native_candidate_preview import NativeCandidateContracts

HERE = Path(__file__).resolve().parents[1] / 'experiments/qt_quick'


class Context(QObject):
    startupAnnouncement = Signal(object)
    busy = Property(bool, lambda s: False, constant=True)
    checking = Property(bool, lambda s: False, constant=True)
    inviteVisible = Property(bool, lambda s: False, constant=True)
    active = Property(bool, lambda s: False, constant=True)
    version = Property(str, lambda s: 'private-fixture', constant=True)
    homePreviews = Property('QVariantList', lambda s: [], constant=True)
    seasonOptions = Property('QVariantList', lambda s: [], constant=True)
    persistedValues = Property('QVariantMap', lambda s: dict(gameSeason='s1', gameMode='nightmare'), constant=True)
    values = Property('QVariantMap', lambda s: dict(sheetOpacity=1.), constant=True)


class CandidateQmlContracts(unittest.TestCase):
    def setUp(self):
        self.producer = NativeCandidateContracts()
        self.producer.setUp(); self.addCleanup(self.producer.doCleanups)
        self.runtime = self.producer.runtime
        self.session = SessionService(self.runtime); self.addCleanup(self.session.shutdown)
        self.fixture = CoordinatorTests(); self.fixture.app = qml.APP
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.coordinator = self.fixture.coordinator
        self.coordinator.runtime = self.runtime
        self.coordinator.session = self.session
        self.coordinator.capture = self.producer.capture
        self.coordinator._bound = ''
        self.coordinator._applied_context = ('nightmare', '', 's1')
        self.session.actionRequested.connect(self.coordinator._action_requested)
        self.session.changed.connect(self.coordinator._session_changed)
        self.runtime.set_event_callback(self.coordinator.runtimeEvent.emit)
        self.addCleanup(self.runtime.set_event_callback, None)

    def settle(self):
        deadline = time.monotonic() + 5
        while self.session.busy and time.monotonic() < deadline:
            qml.APP.processEvents(); QTest.qWait(2)
        self.assertFalse(self.session.busy)

    def test_home_start_button_click_uses_real_session_for_each_s1_mode(self):
        engine = QQmlEngine(); context = Context()
        for name, value in dict(session=self.session, clientCoordinator=self.coordinator,
                infoService=context, settingsService=context, resourceService=context,
                tutorialService=context, onboardingService=context).items():
            engine.rootContext().setContextProperty(name, value)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(HERE / 'ClientHome.qml')))
        page = component.create(); self.assertIsNotNone(page, component.errors())
        window = QQuickWindow(); window.resize(1000, 900)
        page.setParentItem(window.contentItem()); page.setWidth(1000); page.setHeight(900)
        window.show()
        def find(item):
            if item.objectName() == 'clientStartRound': return item
            for child in item.childItems():
                result = find(child)
                if result is not None: return result
        try:
            for mode in ('difficulty2', 'difficult', 'nightmare'):
                self.runtime.dispatch('end_round')
                self.producer.registry.engine.pack.mode = mode
                self.producer.registry.catalog = replace(self.producer.registry.catalog, mode=mode)
                self.runtime.dispatch('set_context', dict(season='s1', mode=mode))
                self.session.refreshRuntime(); qml.APP.processEvents()
                button = find(page); self.assertIsNotNone(button)
                self.assertTrue(button.isEnabled(), (mode, self.session.snapshot))
                self.assertIsNone(self.coordinator._map_resource_problem())
                point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
                self.settle()
                self.assertTrue(self.session.snapshot['lastReceipt']['success'], self.session.snapshot)
                self.assertTrue(self.runtime.snapshot()['roundPreparing'])
                self.assertEqual(self.runtime._map_id, '')
                self.assertFalse(self.runtime.snapshot()['identityLocked'])
        finally:
            window.hide(); page.setParentItem(None); page.deleteLater(); window.deleteLater()
            qml.APP.processEvents()

    def test_formal_aligned_consumer_has_pixels_before_scan_completion_and_clears(self):
        entered, release = threading.Event(), threading.Event()
        def fit(frame, mid, *a, **kw):
            if mid == 'b':
                entered.set(); self.assertTrue(release.wait(5))
            return self.producer.fit
        self.producer.registry.engine.qt_s1_structure_scan = lambda frame, **kw: StructureCandidateScanner(
            self.producer.registry.catalog, fit).scan(frame, **kw)
        images = LiveImages(); self.coordinator.images = images
        windows = WindowController(); self.coordinator.windows = windows
        self.coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        engine = QQmlApplicationEngine(); context = Context()
        engine.addImageProvider('live', images)
        engine.rootContext().setContextProperty('clientCoordinator', self.coordinator)
        engine.rootContext().setContextProperty('settingsService', context)
        engine.load(QUrl.fromLocalFile(str(HERE / 'ClientAligned.qml')))
        self.assertEqual(len(engine.rootObjects()), 1)
        window = engine.rootObjects()[0]; windows.bindWindow('aligned', window)
        self.runtime.dispatch('start_round')
        results = []
        worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('capture_now')))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                qml.APP.processEvents(); QTest.qWait(10)
                if window.isVisible() and self.coordinator.alignedData.get('routeSource'): break
            captured = window.grabWindow().convertToFormat(QImage.Format_RGBA8888)
            self.assertFalse(captured.isNull())
            pixels = np.frombuffer(captured.bits(), np.uint8).reshape(captured.height(), captured.bytesPerLine())
            self.assertGreater(np.count_nonzero(pixels[:, 3:captured.width()*4:4]), 0)
            self.assertTrue(worker.is_alive(), 'visible pixels must precede scan completion')
            self.assertFalse(self.runtime.snapshot()['identityLocked'])
            self.runtime.dispatch('close_map'); qml.APP.processEvents()
            self.assertFalse(window.isVisible())
        finally:
            release.set(); worker.join(5); window.hide()
            self.coordinator.windows = self.fixture.windows


if __name__ == '__main__': unittest.main()
