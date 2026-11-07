"""Extracted Home identity bindings; synthetic state only, not layout/game QA."""
from pathlib import Path
import unittest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent
import test_client_recognition as fixture


class NativeRecognitionPresentation(unittest.TestCase):
    def setUp(self):
        from PySide6.QtQml import QQmlEngine
        self.session = fixture.FakeSession()
        self.engine = QQmlEngine()
        self.engine.rootContext().setContextProperty("session", self.session)

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.engine.deleteLater(); fixture.APP.processEvents()

    def publish(self, **changes):
        self.session.state.update(changes)
        self.session.changed.emit()
        fixture.APP.processEvents()

    def test_home_binding_contract_offscreen(self):
        # Evaluate the exact Home identity properties, without claiming Home layout/service coverage.
        source = Path(__file__).with_name('ClientHome.qml').read_text()
        start = source.index('    readonly property var privateExperiment:')
        end = source.index('    readonly property bool wide:', start)
        component = QQmlComponent(self.engine)
        component.setData(('import QtQuick\nItem {\n' + source[start:end] + '\n}').encode(), QUrl())
        self.assertFalse(component.isError(), str(component.errors()))
        home = component.create()
        self.assertIsNotNone(home)
        try:
            self.publish(season='s1', roundActive=True,
                privateExperiment=dict(privateExperimental=True, privateExperimentalLocked=True, privateLockMapId='101'),
                displayAuthorized=False, provisionalMapId='101')
            self.assertTrue(home.property('privateIdentityRetained'))
            self.assertIn('位置待核对', home.property('privateStatus'))
            self.publish(displayAuthorized=True, provisionalMapId='102')
            self.assertIn('位置待核对', home.property('privateStatus'))
            self.publish(provisionalMapId='101')
            self.assertIn('当前画面已贴合', home.property('privateStatus'))
            self.publish(season='s0')
            self.assertFalse(home.property('privateIdentityRetained'))
            self.publish(season='s1')
            self.publish(roundActive=False)
            self.assertFalse(home.property('privateIdentityRetained'))
        finally:
            home.deleteLater(); fixture.APP.processEvents()


class PreservationSourceContracts(unittest.TestCase):
    def test_main_fixture_and_ci_coverage_are_preserved(self):
        from s0_test_fixtures import synthetic_read
        from floor_control_region import VERTICAL_SEARCH_AREA
        self.assertEqual(synthetic_read()['controlSearchRegion'], list(VERTICAL_SEARCH_AREA))
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/qt-next-integration-windows.yml').read_text()
        for name in ('test_s0_vertical_controls', 'test_s0_vertical_detection', 'test_floor_contract',
                     'test_floor_observation_evidence', 'test_client_s0_vertical', 'test_client_game_floor_click'):
            self.assertIn("'" + name + "'", workflow)
        self.assertIn("'season-ui'", workflow)



if __name__ == '__main__':
    unittest.main()
