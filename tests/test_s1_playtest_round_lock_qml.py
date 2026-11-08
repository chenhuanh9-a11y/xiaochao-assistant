"""Real packaged-purpose runtime -> Session -> Qt/QML consumers, synthetic IO."""
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QUrl, QCoreApplication, QEvent
from PySide6.QtQml import QQmlComponent, QQmlEngine
from client_coordinator import LiveImages
import test_client_recognition as qml
import test_s1_native_candidate_qml as candidate_qml
import test_s1_playtest_round_lock as playtest


class PlaytestLockPresentation(unittest.TestCase):
    def setUp(self):
        with patch.object(candidate_qml, 'NativeCandidateContracts', playtest.PlaytestRoundLockContracts):
            candidate_qml.CandidateQmlContracts.setUp(self)
        self.coordinator.images = LiveImages()
        self.coordinator._convert_client_geometry = lambda row: row.update(deviceScale=1.)
        self.notices = []
        self.coordinator.notificationRequested.connect(lambda *row: self.notices.append(row))

    def settle(self):
        until = time.monotonic() + 5
        while (self.runtime.private_evidence_busy or self.session.busy) and time.monotonic() < until:
            qml.APP.processEvents(); time.sleep(.002)
        self.session.refreshRuntime(); qml.APP.processEvents()
        self.assertFalse(self.runtime.private_evidence_busy)
        self.assertFalse(self.session.busy)

    def test_final_lock_and_sparse_floor_reach_real_qml_without_candidate_status(self):
        engine = QQmlEngine(); context = candidate_qml.Context()
        for name, value in dict(session=self.session, clientCoordinator=self.coordinator,
                settingsService=context, infoService=context, resourceService=context,
                tutorialService=context, onboardingService=context).items():
            engine.rootContext().setContextProperty(name, value)
        components, roots = [], []
        try:
            for file in ('ClientControls.qml', 'ClientHome.qml'):
                component = QQmlComponent(engine, QUrl.fromLocalFile(str(candidate_qml.HERE / file)))
                root = component.create()
                self.assertIsNotNone(root, component.errors())
                components.append(component); roots.append(root)
            self.producer.locked(); self.settle()
            self.assertEqual([n[0] for n in self.notices if '锁定' in n[0]], ['地图已锁定'])
            self.assertTrue(self.session.snapshot['identityLocked'])
            self.assertTrue(self.coordinator.alignedData['identityLocked'])
            self.assertEqual(self.coordinator.alignedData['identityPhase'], 'MEMBER_VERIFIED')
            self.assertFalse(self.coordinator.alignedData['provisional'])
            self.assertIn('已锁定', self.coordinator.referenceData['title'])
            for floor in ('-2', '-3', '-4'):
                self.producer.floor = floor; self.producer.unknown.add('a')
                self.assertFalse(self.runtime.dispatch('observe_candidate_floor')['success'])
                self.settle()
                controls, home = roots
                self.assertIn('地图已锁定', controls.findChild(QObject, 'controlStatusText').property('text'))
                self.assertNotIn('候选', controls.findChild(QObject, 'controlMapFloorText').property('text'))
                self.assertFalse(home.property('privateIdentityRetained'))
                self.assertFalse(controls.property('unconfirmedCandidate'))
                self.assertFalse(self.coordinator.alignedData)
                self.assertEqual(self.coordinator.referenceData['mapId'], 'a')
                self.assertEqual(self.coordinator.referenceData['floor'], floor)
                self.assertIn('已锁定', self.coordinator.referenceData['title'])
            self.assertFalse(any('恢复' in n[0] or '矛盾' in n[0] for n in self.notices))
            self.runtime.dispatch('end_round'); self.settle()
            self.assertFalse(self.session.snapshot['identityLocked'])
            self.assertFalse(self.coordinator.referenceData)
        finally:
            for root in roots:
                if hasattr(root, 'close'): root.close()
                root.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            qml.APP.processEvents()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            qml.APP.processEvents()


if __name__ == '__main__':
    unittest.main()
