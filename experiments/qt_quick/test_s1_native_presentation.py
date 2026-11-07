"""Offscreen real recognition page and extracted Home bindings; synthetic state only."""
from pathlib import Path
import unittest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent
import test_client_recognition as fixture


class NativeRecognitionPresentation(unittest.TestCase):
    setUp = fixture.RecognitionTests.setUp
    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        fixture.RecognitionTests.tearDown(self)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        fixture.APP.processEvents()
    item = fixture.RecognitionTests.item

    def publish(self, **changes):
        self.session.state.update(changes)
        self.session.changed.emit()
        fixture.APP.processEvents()

    def test_retained_identity_requires_fresh_pose_and_never_dispatches(self):
        self.publish(season='s1', roundActive=True, candidateProvisionalDisplayEnabled=True,
            privateExperiment=dict(privateExperimental=True, privateExperimentalLocked=True, privateLockMapId='101'),
            displayAuthorized=False, provisionalMapId='101')
        label = self.item('recognitionIdentitySummary')
        self.assertIn('位置待核对', label.property('text'))
        self.assertIn('本局 101', label.property('text'))
        self.assertNotIn('未锁定候选', label.property('text'))
        self.publish(displayAuthorized=True, provisionalMapId='102')
        self.assertIn('位置待核对', label.property('text'))
        self.publish(provisionalMapId='101')
        self.assertIn('当前画面已贴合', label.property('text'))
        self.publish(displayAuthorized=False)
        self.assertIn('位置待核对', label.property('text'))
        self.assertTrue(self.page.property('privateIdentityRetained'))
        self.assertEqual(self.session.calls, [])
        self.assertFalse(self.warnings)

    def test_old_round_or_other_season_never_exposes_retained_lock(self):
        self.publish(season='s1', roundActive=False,
            privateExperiment=dict(privateExperimental=True, privateExperimentalLocked=True, privateLockMapId='101'))
        self.assertFalse(self.page.property('privateIdentityRetained'))
        self.publish(season='s0', roundActive=True)
        self.assertFalse(self.page.property('privateIdentityRetained'))
        self.publish(season='s1', privateExperiment={})
        self.assertFalse(self.page.property('privateIdentityRetained'))
        self.assertEqual(self.session.calls, [])

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
            self.publish(displayAuthorized=True)
            self.assertIn('当前画面已贴合', home.property('privateStatus'))
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

    def test_presentation_does_not_add_actions_or_claim_live_validation(self):
        source = Path(__file__).with_name('ClientRecognition.qml').read_text()
        self.assertNotIn('贴合失败时恢复识别', source)
        self.assertIn('静态选中行核验不代表结构贴合或实机通过', source)
        self.assertIn('暂时无法贴合只停止显示', source)
        self.assertNotIn('requestManualMapSelection', source)
        self.assertNotIn('prepare_manual_alignment', source)
        self.assertIn('候选不代表已确认身份', source)


if __name__ == '__main__':
    unittest.main()
